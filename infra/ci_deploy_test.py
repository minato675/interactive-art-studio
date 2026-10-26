"""일회성 rootless Docker에서 실제 배포 스크립트의 교체·복구를 검사한다."""
import argparse
import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import tempfile
import time
import xml.etree.ElementTree as ET
from pathlib import Path

CENTRAL = Path(__file__).resolve().parent / "central"
SERVICES = ("central", "worker", "dispatcher")


def require_disposable_engine(info):
    """현재 노트북/EC2 Docker를 잘못 지정하면 배포 전에 중단한다."""
    if "ranel.ci.disposable=true" not in (info.get("Labels") or []):
        raise RuntimeError("A disposable CI Docker daemon is required")
    if not any("rootless" in option for option in info.get("SecurityOptions", [])):
        raise RuntimeError("The CI Docker daemon must run rootless")
    if info.get("Containers", 0):
        raise RuntimeError("The disposable daemon must not contain existing containers")


class DeploymentCheck:
    def __init__(self, image, output, root):
        if not re.fullmatch(r"[a-zA-Z0-9./:_-]+:[a-f0-9]{40}", image):
            raise ValueError("Candidate image requires a full commit SHA tag")
        self.image, self.output, self.root = image, output, root
        self.project = "ranel-ci-" + secrets.token_hex(6)
        self.secrets = [secrets.token_hex(24) for _ in range(3)]
        self.env_file = root / ".env.infra"
        self.env_file.write_text(
            f"POSTGRES_PASSWORD={self.secrets[0]}\nRABBITMQ_PASSWORD={self.secrets[1]}\n"
            f"OPERATOR_TOKEN={self.secrets[2]}\nAGENT_KEYS_JSON={{}}\n", encoding="utf-8")
        self.initial_env = self.env_file.read_bytes()
        self.environment = {**os.environ, "APP_IMAGE": image, "RANEL_DEPLOY_ROOT": str(root),
                            "RANEL_COMPOSE_PROJECT": self.project,
                            "RANEL_COMPOSE_OVERRIDE": str(CENTRAL / "compose.ci.yaml"),
                            "RANEL_DEPLOY_PULL_POLICY": "never", "RANEL_DEPLOY_WAIT_TIMEOUT": "90",
                            "RANEL_DEPLOY_SMOKE_TIMEOUT": "15"}
        self.compose = ["docker", "compose", "--project-name", self.project,
                        "--env-file", str(self.env_file), "-f", str(CENTRAL / "compose.yaml"),
                        "-f", str(CENTRAL / "compose.ci.yaml")]
        self.phase = "prepare"
        self.results = []
        self.marker = secrets.token_hex(12)
        self.output.mkdir(parents=True, exist_ok=True)

    def redact(self, value):
        for secret in self.secrets:
            value = value.replace(secret, "[redacted]")
        return value

    def run(self, command, *, input=None, expected=0, timeout=240):
        result = subprocess.run(command, input=input, capture_output=True, text=True, check=False,
                                env=self.environment, timeout=timeout)
        with (self.output / f"{self.phase}.log").open("a", encoding="utf-8") as log:
            log.write(self.redact(result.stdout + result.stderr))
        if result.returncode != expected:
            raise RuntimeError(f"{self.phase}: {command[:3]} returned {result.returncode}, expected {expected}")
        return result.stdout.strip()

    def exec(self, service, *command, input=None):
        return self.run([*self.compose, "exec", "-T", service, *command], input=input)

    def image_id(self, image):
        return self.run(["docker", "image", "inspect", "--format", "{{.Id}}", image])

    def deploy(self, image, *, failure=False):
        output = self.run(["bash", str(CENTRAL / "deploy.sh"), image], expected=1 if failure else 0, timeout=360)
        if failure and ("Deployment failed;" not in output or "Previous image restored." not in output):
            raise RuntimeError("Failure did not reach and complete the rollback branch")

    def assert_active(self, image):
        expected_id = self.image_id(image)
        for service in SERVICES:
            container = self.run([*self.compose, "ps", "-q", service])
            if not container:
                raise RuntimeError(f"Missing service: {service}")
            state = json.loads(self.run(["docker", "inspect", "--format",
                                        '{"Image":{{json .Image}},"Running":{{.State.Running}}}', container]))
            if state["Image"] != expected_id or not state["Running"]:
                raise RuntimeError(f"{service} did not run the expected image")
        if (self.root / "current-image").read_text().strip() != image:
            raise RuntimeError("current-image changed to an unapproved image")
        if self.env_file.read_bytes() != self.initial_env:
            raise RuntimeError("Deployment changed credentials")

    def assert_data(self):
        value = self.exec("postgres", "psql", "-U", "ranel", "-d", "ranel", "-Atc",
                          "SELECT marker FROM ci_deploy_marker")
        if value != self.marker:
            raise RuntimeError("Database marker was not preserved")
        value = self.exec("central", "python", "-c",
                          "from pathlib import Path; print(Path('/data/ci-marker').read_text())")
        if value != self.marker:
            raise RuntimeError("Data volume file was not preserved")

    def test_image(self, name, body):
        tag = f"ranel-ci:{hashlib.sha1((self.image + name).encode()).hexdigest()}"
        self.run(["docker", "build", "-t", tag, "-"], input=f"FROM {self.image}\n{body}\n", timeout=300)
        return tag

    def step(self, name, action):
        self.phase = name
        started = time.monotonic()
        try:
            action()
        except Exception as exc:
            self.results.append({"name": name, "seconds": time.monotonic() - started,
                                 "status": "failed", "error": self.redact(str(exc))})
            raise
        else:
            self.results.append({"name": name, "seconds": time.monotonic() - started, "status": "passed"})
            print(json.dumps(self.results[-1]), flush=True)

    def collect(self):
        for name, args in (("containers", ["ps", "--all"]),
                           ("services", ["logs", "--no-color", "--tail", "80"])):
            try:
                result = subprocess.run([*self.compose, *args], capture_output=True, text=True, check=False,
                                        env=self.environment, timeout=30)
                (self.output / f"{self.phase}-{name}.log").write_text(
                    self.redact(result.stdout + result.stderr), encoding="utf-8")
            except subprocess.TimeoutExpired:
                pass

    def report(self):
        (self.output / "result.json").write_text(json.dumps({"candidate": self.image,
            "project": self.project, "checks": self.results}, indent=2), encoding="utf-8")
        suite = ET.Element("testsuite", name="image-deployment", tests=str(len(self.results)),
                           failures=str(sum(r["status"] == "failed" for r in self.results)))
        for result in self.results:
            case = ET.SubElement(suite, "testcase", name=result["name"], classname="ci.deployment",
                                 time=f"{result['seconds']:.3f}")
            if result["status"] == "failed":
                ET.SubElement(case, "failure", message=result["error"])
        ET.ElementTree(suite).write(self.output / "junit.xml", encoding="utf-8", xml_declaration=True)

    def check(self):
        info = json.loads(self.run(["docker", "info", "--format", "{{json .}}"], timeout=30))
        require_disposable_engine(info)
        try:
            self.step("prepare-images", self.prepare)
            self.step("baseline-start", lambda: self.deploy_and_check(self.baseline))
            self.step("candidate-upgrade", self.upgrade)
            self.step("startup-failure-rollback", lambda: self.failed_deploy(self.broken))
            self.step("worker-failure-rollback", lambda: self.failed_deploy(self.stalled))
            self.step("source-build-deploy", self.source_build)
            self.step("compose-failure-rollback", self.failed_compose)
        finally:
            self.collect()
            try:
                self.step("cleanup", self.cleanup)
            finally:
                self.report()

    def prepare(self):
        self.run([*self.compose, "pull", "postgres", "rabbitmq"], timeout=300)
        self.baseline = self.test_image("baseline", 'LABEL ranel.ci.baseline="true"')
        self.broken = self.test_image("startup-failure", 'ENTRYPOINT ["python", "-c", "raise SystemExit(42)"]')
        self.stalled = self.test_image("worker-failure", 'ENTRYPOINT ["python", "-c", '
            '"import os,sys,time; time.sleep(300) if sys.argv[1] == \'celery\' '
            'else os.execvp(sys.argv[1], sys.argv[1:])"]\n'
            'CMD ["uvicorn", "central.main:app", "--host", "0.0.0.0", "--port", "8000"]')

    def deploy_and_check(self, image):
        self.deploy(image)
        self.assert_active(image)

    def upgrade(self):
        self.exec("postgres", "psql", "-v", "ON_ERROR_STOP=1", "-U", "ranel", "-d", "ranel", "-c",
                  f"CREATE TABLE ci_deploy_marker(marker text); INSERT INTO ci_deploy_marker VALUES ('{self.marker}')")
        self.exec("central", "python", "-c",
                  f"from pathlib import Path; Path('/data/ci-marker').write_text('{self.marker}')")
        if self.image_id(self.baseline) == self.image_id(self.image):
            raise RuntimeError("Upgrade must switch to a different image ID")
        self.deploy_and_check(self.image)
        self.assert_data()

    def failed_deploy(self, image):
        self.deploy(image, failure=True)
        if image == self.stalled:
            log = (self.output / f"{self.phase}.log").read_text(encoding="utf-8")
            if "Worker did not finish within" not in log:
                raise RuntimeError("Expected a running API with a stalled Worker, not a startup failure")
        self.assert_active(self.image)
        self.assert_data()
        self.collect()

    def source_build(self):
        image = "ranel-ci-source:" + hashlib.sha1((self.image + "source").encode()).hexdigest()
        self.environment["RANEL_DEPLOY_BUILD"] = "1"
        try:
            self.deploy(image)
        finally:
            self.environment.pop("RANEL_DEPLOY_BUILD", None)
        self.image = image
        self.assert_active(image)
        self.assert_data()

    def failed_compose(self):
        candidate = self.root / "bad-release" / "infra" / "central"
        candidate.mkdir(parents=True)
        for filename in ("compose.yaml", "deploy.sh", "smoke.py"):
            shutil.copyfile(CENTRAL / filename, candidate / filename)
        text = (candidate / "compose.yaml").read_text(encoding="utf-8")
        if "  central:\n    <<: *app" not in text:
            raise RuntimeError("Cannot construct the intentional Compose failure")
        text = text.replace("  central:\n    <<: *app", '  central:\n    <<: *app\n    entrypoint: ["false"]')
        (candidate / "compose.yaml").write_text(text, encoding="utf-8")
        self.environment["RANEL_DEPLOY_WAIT_TIMEOUT"] = "15"
        try:
            output = self.run(["bash", str(candidate / "deploy.sh"), self.image], expected=1, timeout=180)
        finally:
            self.environment["RANEL_DEPLOY_WAIT_TIMEOUT"] = "90"
        if "Previous image restored." not in output:
            raise RuntimeError("Changed Compose was not rolled back")
        if (self.root / "current-release").read_text().strip() != str(CENTRAL):
            raise RuntimeError("Failed Compose became the active release")
        self.assert_active(self.image)
        self.assert_data()

    def cleanup(self):
        self.run([*self.compose, "down", "--volumes", "--remove-orphans"], timeout=90)
        if self.run(["docker", "ps", "-aq", "--filter", f"label=com.docker.compose.project={self.project}"]):
            raise RuntimeError("Temporary containers remain")
        if self.run(["docker", "volume", "ls", "-q", "--filter", f"label=com.docker.compose.project={self.project}"]):
            raise RuntimeError("Temporary data volumes remain")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", required=True)
    parser.add_argument("--output", type=Path, default=Path("runtime/ci-deploy"))
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="ranel-ci-deploy-") as folder:
        DeploymentCheck(args.image, args.output, Path(folder)).check()


if __name__ == "__main__":
    main()

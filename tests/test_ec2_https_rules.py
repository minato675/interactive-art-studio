import pytest
from infra.configure_ec2_https import rules


def test_https_firewall_rules_only_replace_own_chain():
    table, clients = rules(["211.192.252.228/32", "211.192.210.218/32", "211.192.252.228/32"])
    assert len(clients) == 2
    assert "-F RANEL-HTTPS\n" in table
    assert "-F DOCKER" not in table and "-F INPUT" not in table
    assert table.endswith("-A RANEL-HTTPS -j DROP\nCOMMIT\n")
    assert table.count("-j RETURN") == 2


@pytest.mark.parametrize("addresses", [[], ["0.0.0.0/0"], ["211.192.252.0/24"], ["::1/128"], ["not-an-ip"]])
def test_https_firewall_requires_explicit_ipv4_hosts(addresses):
    with pytest.raises(ValueError):
        rules(addresses)


def test_public_https_releases_only_the_managed_chain():
    table, clients = rules(public=True)
    assert clients == []
    assert table == "*filter\n:RANEL-HTTPS - [0:0]\n-F RANEL-HTTPS\n-A RANEL-HTTPS -j RETURN\nCOMMIT\n"
    assert "DOCKER-USER" not in table and "INPUT" not in table


def test_public_mode_cannot_silently_mix_an_allowlist():
    with pytest.raises(ValueError):
        rules(["192.0.2.1/32"], public=True)

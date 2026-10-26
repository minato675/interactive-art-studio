param(
    [Parameter(Mandatory)][string]$ServerAddress,
    [Parameter(Mandatory)][string[]]$ClientAddresses,
    [ValidateRange(1024,65535)][int]$Port = 8443,
    [string]$ResultPath = ''
)
$ErrorActionPreference = 'Stop'
function ConvertTo-AddressNumber([string]$Address) {
    $bytes = [Net.IPAddress]::Parse($Address).GetAddressBytes()
    return ([uint64]$bytes[0] * 16777216 + [uint64]$bytes[1] * 65536 + [uint64]$bytes[2] * 256 + $bytes[3])
}
function ConvertFrom-AddressNumber([uint64]$Number) {
    return ('{0}.{1}.{2}.{3}' -f (($Number -shr 24) -band 255), (($Number -shr 16) -band 255), (($Number -shr 8) -band 255), ($Number -band 255))
}
try {
    $principal = [Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw 'Run this script in an Administrator PowerShell window.'
    }
    $serverIp = [Net.IPAddress]::Parse($ServerAddress)
    if ($serverIp.AddressFamily -ne [Net.Sockets.AddressFamily]::InterNetwork) { throw 'IPv4 is required.' }
    foreach ($address in $ClientAddresses) {
        $clientIp = [Net.IPAddress]::Parse($address)
        if ($clientIp.AddressFamily -ne [Net.Sockets.AddressFamily]::InterNetwork) { throw 'IPv4 is required.' }
    }
    if (-not (Get-NetIPAddress -AddressFamily IPv4 -IPAddress $ServerAddress -ErrorAction SilentlyContinue)) {
        throw 'ServerAddress is not assigned to this computer.'
    }
    # A pre-existing Docker Allow rule may permit all peers. Scope this Block rule
    # only to the central HTTPS socket, leaving unrelated Docker ports unchanged.
    $permitted = @(@($ClientAddresses) + @($ServerAddress) | ForEach-Object { ConvertTo-AddressNumber $_ } | Sort-Object -Unique)
    $blocked = @()
    [uint64]$next = 0
    foreach ($number in $permitted) {
        if ($next -lt $number) {
            $blocked += ('{0}-{1}' -f (ConvertFrom-AddressNumber $next), (ConvertFrom-AddressNumber ($number - 1)))
        }
        $next = $number + 1
    }
    if ($next -le 4294967295) {
        $blocked += ('{0}-255.255.255.255' -f (ConvertFrom-AddressNumber $next))
    }
    $blockName = 'Ranel-Temporary-Central-HTTPS-OtherPeers'
    $blockRule = @{
        DisplayName = 'Ranel central HTTPS: block other peers'
        Description = 'Only the central HTTPS socket; existing Docker rules are preserved'
        Enabled = 'True'; Profile = 'Any'; Direction = 'Inbound'; Action = 'Block'
        Protocol = 'TCP'; LocalPort = $Port; LocalAddress = $ServerAddress
        RemoteAddress = $blocked; EdgeTraversalPolicy = 'Block'
    }
    if (Get-NetFirewallRule -Name $blockName -ErrorAction SilentlyContinue) {
        $blockRule.NewDisplayName = $blockRule.DisplayName
        $blockRule.Remove('DisplayName')
        Set-NetFirewallRule -Name $blockName @blockRule | Out-Null
    } else {
        New-NetFirewallRule -Name $blockName @blockRule | Out-Null
    }
    $rule = @{
        DisplayName = 'Ranel temporary central HTTPS'
        Description = 'Temporary central API: explicit generation/display client addresses only'
        Enabled = 'True'; Profile = 'Any'; Direction = 'Inbound'; Action = 'Allow'
        Protocol = 'TCP'; LocalPort = $Port; LocalAddress = $ServerAddress
        RemoteAddress = $ClientAddresses; EdgeTraversalPolicy = 'Block'
    }
    $name = 'Ranel-Temporary-Central-HTTPS'
    if (Get-NetFirewallRule -Name $name -ErrorAction SilentlyContinue) {
        $rule.NewDisplayName = $rule.DisplayName
        $rule.Remove('DisplayName')
        Set-NetFirewallRule -Name $name @rule | Out-Null
    } else {
        New-NetFirewallRule -Name $name @rule | Out-Null
    }
    $result = @{ ok = $true; rule = $name; block_rule = $blockName; server = $ServerAddress; port = $Port; clients = $ClientAddresses }
    if ($ResultPath) { $result | ConvertTo-Json | Set-Content -LiteralPath $ResultPath -Encoding UTF8 }
    $result | ConvertTo-Json
} catch {
    if ($ResultPath) {
        @{ ok = $false; error = $_.Exception.Message } | ConvertTo-Json | Set-Content -LiteralPath $ResultPath -Encoding UTF8
    }
    throw
}

param([switch]$FromClipboard)
$ErrorActionPreference = 'Stop'
if ($FromClipboard) {
    $ClipboardToken = (Get-Clipboard -Raw).Trim()
    $SecureToken = ConvertTo-SecureString $ClipboardToken -AsPlainText -Force
    $ClipboardToken = $null
} else {
    $SecureToken = Read-Host 'Paste the NEW Shotsnip Meta token (hidden)' -AsSecureString
}
$TokenPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($SecureToken)
try {
    $ShotsnipToken = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($TokenPointer).Trim()
    if ($ShotsnipToken.Length -lt 40 -or $ShotsnipToken -match '\s') {
        throw 'The full token was not received. Paste the complete token, then press Enter.'
    }
    [Environment]::SetEnvironmentVariable('SHOTSNIP_META_TOKEN', $ShotsnipToken, 'User')
    Write-Host 'Saved for this Windows account. The worker reads it on its next cross-post.'
} finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($TokenPointer)
    $ShotsnipToken = $null
}

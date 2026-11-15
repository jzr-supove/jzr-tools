# Install jzr-tools on Windows:
#
#   powershell -ExecutionPolicy ByPass -c "irm https://raw.githubusercontent.com/jzr-supove/jzr-tools/main/install.ps1 | iex"
#
# Installs uv (https://docs.astral.sh/uv/) when it is missing; uv then
# downloads a suitable Python by itself, so Python does not have to be
# installed first. Running it again upgrades jzr-tools.
#
# It runs inside the caller's session (irm | iex), so it never calls `exit`
# and keeps its settings inside this script block.
& {
    # Not $ErrorActionPreference = 'Stop': Windows PowerShell 5.1 can turn a
    # native program's stderr (uv writes its progress there) into an error.
    function Fail([string] $reason) {
        throw "jzr-tools was not installed: $reason"
    }

    if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
        Write-Host 'Installing uv...'
        try {
            Invoke-RestMethod https://astral.sh/uv/install.ps1 -ErrorAction Stop | Invoke-Expression
        } catch {
            Fail "the uv installer failed: $($_.Exception.Message)"
        }
        # This session has not picked up the new PATH yet.
        $candidates = @($env:UV_INSTALL_DIR, $env:XDG_BIN_HOME, (Join-Path $HOME '.local\bin')) |
            Where-Object { $_ }
        foreach ($dir in $candidates) {
            if (Test-Path (Join-Path $dir 'uv.exe')) {
                $env:Path = "$dir;$env:Path"
                break
            }
        }
        if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
            Fail 'uv was installed but cannot be found; open a new terminal and run this again'
        }
    }

    Write-Host 'Installing jzr-tools...'
    & uv tool install --upgrade jzr-tools
    if ($LASTEXITCODE -ne 0) {
        Fail 'uv tool install failed (see above)'
    }
    # Make sure the folder with the jzr command is on PATH for new terminals.
    & uv tool update-shell 2>&1 | Out-Null

    Write-Output ''
    Write-Output 'Done. Open a new terminal and run: jzr --help'
    Write-Output 'Video and audio commands need ffmpeg; if you do not have it, run: jzr ffmpeg install'
}

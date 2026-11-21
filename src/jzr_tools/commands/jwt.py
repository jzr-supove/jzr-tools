"""`jzr jwt`: show what a JSON Web Token says, and check an HMAC signature."""

from __future__ import annotations

import os
from pathlib import Path

import click

_NOT_CHECKED = (
    "signature not checked (give --secret-env or --secret-file to check an HMAC signature)"
)


@click.command(
    "jwt",
    context_settings={"help_option_names": ["-h", "--help"]},
    short_help="Decode a JWT and check its times and HMAC signature.",
)
@click.argument("token", required=False)
@click.option(
    "--secret-env",
    metavar="NAME",
    default=None,
    help="Check an HS256/384/512 signature with the secret in this environment variable.",
)
@click.option(
    "--secret-file",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="Check the signature with the secret in this file (one final line break is dropped).",
)
@click.option(
    "--require-valid",
    is_flag=True,
    help="Exit 1 when the token is expired, not yet valid, or its signature is invalid.",
)
@click.option("--json", "as_json", is_flag=True, help="Print the result as JSON.")
def jwt_command(
    token: str | None,
    secret_env: str | None,
    secret_file: Path | None,
    require_valid: bool,
    as_json: bool,
) -> None:
    """Decode a JWT and check its times and HMAC signature.

    TOKEN may start with "Authorization: Bearer"; without TOKEN (or with -)
    it is read from stdin. Decoding never proves a token is genuine: give
    the secret with --secret-env or --secret-file (never on the command
    line) to check an HMAC signature.
    """
    secret = _secret(secret_env, secret_file)

    import json

    from jzr_tools.core import jwt as jwtc
    from jzr_tools.core import timestamp

    try:
        decoded = jwtc.decode(_stdin_text() if token in (None, "-") else token)
    except jwtc.JwtError as error:
        click.echo(str(error), err=True)
        raise click.exceptions.Exit(1) from None

    claims = jwtc.time_claims(decoded.payload, timestamp.now())
    signature = "not checked"
    check_error: str | None = None
    if secret is not None:
        try:
            signature = "valid" if jwtc.verify(decoded, secret) else "invalid"
        except jwtc.JwtError as error:
            check_error = str(error)

    if as_json:
        document = {
            "header": decoded.header,
            "payload": decoded.payload,
            "claims": {claim.name: _claim_json(claim) for claim in claims},
            "signature": signature,
        }
        click.echo(json.dumps(document, indent=2, ensure_ascii=False))
    else:
        click.echo("Header")
        click.echo(json.dumps(decoded.header, indent=2, ensure_ascii=False))
        click.echo()
        click.echo("Payload")
        click.echo(json.dumps(decoded.payload, indent=2, ensure_ascii=False))
        click.echo()
        for claim in claims:
            click.echo(_claim_line(claim))
        if signature != "not checked":
            click.echo("signature valid" if signature == "valid" else "signature INVALID")

    if secret is None:
        click.echo(_NOT_CHECKED, err=True)
    failed = False
    if check_error is not None:
        click.echo(check_error, err=True)
        failed = True
    if signature == "invalid":
        failed = True
    if require_valid:
        exp, _iat, nbf = claims
        reasons = [
            reason
            for reason, applies in (
                ("expired", exp.status == "expired"),
                ("not yet valid", nbf.status == "not yet valid"),
                ("signature invalid", signature == "invalid"),
            )
            if applies
        ]
        if reasons:
            click.echo(f"not valid: {', '.join(reasons)}", err=True)
            failed = True
    if failed:
        raise click.exceptions.Exit(1)


def _secret(env_name: str | None, path: Path | None) -> bytes | None:
    if env_name is not None and path is not None:
        raise click.UsageError("Give --secret-env or --secret-file, not both.")
    if env_name is not None:
        value = os.environ.get(env_name)
        if value is None:
            raise click.UsageError(f"The environment variable {env_name} is not set.")
        # The variable's own bytes on POSIX, UTF-8 on Windows.
        return os.fsencode(value)
    if path is not None:
        try:
            data = path.read_bytes()
        except OSError as error:
            raise click.UsageError(
                f"Cannot read the --secret-file: {error.strerror or error}"
            ) from None
        if data.endswith(b"\r\n"):
            return data[:-2]
        if data.endswith(b"\n"):
            return data[:-1]
        return data
    return None


def _stdin_text() -> str:
    stream = click.get_binary_stream("stdin")
    try:
        terminal = stream.isatty()
    except (AttributeError, ValueError):
        terminal = False
    if terminal:
        click.echo(
            "Paste the token, then finish with Ctrl+Z and Enter on Windows or Ctrl+D on macOS/Linux.",
            err=True,
        )
    return stream.read().decode("utf-8", errors="replace")


def _claim_line(claim) -> str:  # noqa: ANN001 - core.jwt.Claim
    import json

    if not claim.present:
        return f"{claim.name}  not present"
    if claim.problem == "not a number":
        return f"{claim.name}  {json.dumps(claim.value, ensure_ascii=False)} (not a number)"
    if claim.problem == "out of range":
        line = f"{claim.name}  {json.dumps(claim.value)} (out of range)"
    else:
        line = f"{claim.name}  {claim.utc} ({claim.relative})"
    return f"{line}   {claim.status}" if claim.status else line


def _claim_json(claim) -> dict | None:  # noqa: ANN001 - core.jwt.Claim
    if not claim.present:
        return None
    return {
        "value": claim.value,
        "utc": claim.utc,
        "relative": claim.relative,
        "status": claim.status,
        "problem": claim.problem,
    }

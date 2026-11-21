"""`jzr hash`: checksums of files or text, and checking them."""

from __future__ import annotations

import json
from pathlib import Path

import click

_ALGORITHMS = ("md5", "sha1", "sha256", "sha384", "sha512", "sha3-256", "sha3-512", "blake2b")


@click.command(
    "hash",
    context_settings={"help_option_names": ["-h", "--help"]},
    short_help="Hash files or text (SHA-256 by default).",
)
@click.argument("files", nargs=-1)
@click.option("-t", "--text", default=None, help="Hash this text (UTF-8) instead of files.")
@click.option(
    "-a",
    "--algorithm",
    type=click.Choice(_ALGORITHMS, case_sensitive=False),
    default="sha256",
    show_default=True,
)
@click.option("--expect", default=None, metavar="HEX", help="Say OK or MISMATCH for one input.")
@click.option(
    "-c",
    "--check",
    "sum_file",
    type=click.Path(path_type=Path),
    default=None,
    metavar="SUMFILE",
    help="Check the files listed in a sha256sum-style file.",
)
@click.option("--json", "as_json", is_flag=True, help="Print the results as JSON.")
def hash_command(
    files: tuple[str, ...],
    text: str | None,
    algorithm: str,
    expect: str | None,
    sum_file: Path | None,
    as_json: bool,
) -> None:
    """Hash files or text (SHA-256 by default).

    Prints HEX  NAME per file, as sha256sum does; without FILES (or with -)
    hashes stdin. --check reads such a list, with names relative to the
    list's own folder.
    """
    from jzr_tools.core import hashing

    _lf_output()
    algorithm = algorithm.lower()
    if text is not None and (files or sum_file is not None):
        raise click.UsageError("--text hashes the text given; it does not combine with FILES or --check.")
    if sum_file is not None and (files or expect is not None):
        raise click.UsageError(
            "--check reads the files from SUMFILE; it does not combine with FILES or --expect."
        )
    if expect is not None and len(files) > 1:
        raise click.UsageError("--expect checks one input; use --check for several files.")

    expected = None
    if expect is not None:
        try:
            expected = hashing.normalize_hex(expect)
        except hashing.HashError as error:
            raise click.UsageError(f"--expect {error}.") from error

    if sum_file is not None:
        _check(sum_file, algorithm if _algorithm_given() else None, as_json=as_json)
        return

    results: list[tuple[str | None, str]] = []
    failed = False
    if text is not None:
        results.append((None, hashing.hash_bytes(text.encode("utf-8"), algorithm)))
    elif not files or files == ("-",):
        stream = click.get_binary_stream("stdin")
        results.append(("-", hashing.hash_stream(stream, algorithm)))
    else:
        for name in files:
            # Names are printed exactly as typed, so a sum file made on one
            # system keeps its separators.
            path = Path(name)
            if name == "-":
                results.append(("-", hashing.hash_stream(click.get_binary_stream("stdin"), algorithm)))
                continue
            if not path.exists():
                click.echo(f"fail {name}: not found", err=True)
                failed = True
                continue
            if path.is_dir():
                click.echo(f"fail {name}: is a folder", err=True)
                failed = True
                continue
            try:
                results.append((name, hashing.hash_file(path, algorithm)))
            except OSError as error:
                click.echo(f"fail {name}: {error.strerror or error}", err=True)
                failed = True

    if expected is not None:
        if not results:
            raise click.exceptions.Exit(1)
        name, digest = results[0]
        match = digest == expected
        if as_json:
            click.echo(
                json.dumps(
                    {
                        "file": name,
                        "algorithm": algorithm,
                        "hash": digest,
                        "expected": expected,
                        "match": match,
                    },
                    ensure_ascii=False,
                )
            )
        else:
            click.echo("OK" if match else "MISMATCH")
        if not match:
            raise click.exceptions.Exit(1)
        return

    if as_json:
        click.echo(
            json.dumps(
                [{"file": name, "algorithm": algorithm, "hash": digest} for name, digest in results],
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        for name, digest in results:
            # A single stdin or --text input has nothing to name.
            if name is None or (name == "-" and len(results) == 1 and files in ((), ("-",))):
                click.echo(digest)
            else:
                click.echo(hashing.format_sum_line(digest, name))
    if failed:
        raise click.exceptions.Exit(1)


def _lf_output() -> None:
    """End lines with LF even on Windows.

    `jzr hash * > SHA256SUMS` must be readable by sha256sum, which takes a
    trailing CR as part of the file name.
    """
    import sys

    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if reconfigure is not None:
        try:
            reconfigure(newline="\n")
        except (OSError, ValueError):
            pass


def _algorithm_given() -> bool:
    """Whether -a was on the command line (not just its default)."""
    from click.core import ParameterSource

    context = click.get_current_context()
    return context.get_parameter_source("algorithm") is not ParameterSource.DEFAULT


def _check(sum_file: Path, algorithm: str | None, *, as_json: bool) -> None:
    from jzr_tools.core import hashing

    try:
        results = hashing.check_sums(sum_file, algorithm=algorithm)
    except hashing.HashError as error:
        click.echo(str(error), err=True)
        raise click.exceptions.Exit(1) from error
    except OSError as error:
        click.echo(f"cannot read {sum_file}: {error.strerror or error}", err=True)
        raise click.exceptions.Exit(1) from error

    if as_json:
        click.echo(
            json.dumps(
                [{"file": result.name, "status": result.status} for result in results],
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        for result in results:
            if result.status == "unreadable":
                click.echo(f"{result.name}: unreadable ({result.detail})")
            else:
                click.echo(f"{result.name}: {result.status}")
    bad = sum(result.status != "OK" for result in results)
    if bad:
        if not as_json:
            click.echo(f"{bad} of {len(results)} files did not match.", err=True)
        raise click.exceptions.Exit(1)

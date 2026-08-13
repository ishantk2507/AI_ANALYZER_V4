"""Command line interface.

    ai-analyzer doctor
    ai-analyzer datasets
    ai-analyzer ask "top 10 two-wheeler makers in Delhi"
    ai-analyzer chat

Also runnable without installing, as `python -m app.cli <command>`.

Chats and standing preferences are stored per user, so `chat` resumes where it
left off and `remember` outlives the session. `--user` selects whose they are;
on a single-user install the default is the only one that ever exists.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table

from app import cache
from app.config import settings
from app.logging_setup import configure_logging


def _configure_stdio() -> None:
    """Windows consoles default to cp1252, which cannot encode most of what a
    model writes. Without this, a rupee sign or a curly quote in the narrative
    kills the process *after* the analysis has already succeeded."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):  # pragma: no cover - non-reconfigurable stream
            pass


_configure_stdio()

application = typer.Typer(add_completion=False, help="Local AI data analyst.")
console = Console()


@application.command()
def doctor() -> None:
    """Check the environment: model, backends, GPU, data."""
    configure_logging()
    table = Table(show_header=True, header_style="bold")
    table.add_column("Check")
    table.add_column("Result")
    table.add_column("Detail", overflow="fold")

    def row(name, ok, detail=""):
        mark = "[green]ok[/green]" if ok else "[red]problem[/red]"
        table.add_row(name, mark, detail)

    # Which interpreter matters: a project .venv and a system Python can have
    # different packages installed, and only one of them has llama-cpp-python.
    import pandas

    table.add_row("interpreter", "[dim]info[/dim]",
                  f"{sys.executable} (pandas {pandas.__version__})")

    model = settings.resolved_model_path()
    row("model file", model is not None,
        str(model) if model else "not found - set MODEL_PATH in .env")

    try:
        from app.llm.ollama_backend import server_is_up

        up = server_is_up()
        row("ollama server", up,
            settings.ollama_host if up else f"not reachable at {settings.ollama_host}")
    except Exception as exc:
        row("ollama server", False, str(exc))

    try:
        from app.llm.llamacpp_backend import gpu_offload_supported

        gpu = gpu_offload_supported()
        table.add_row(
            "llama-cpp gpu",
            "[green]ok[/green]" if gpu else "[yellow]cpu only[/yellow]",
            "CUDA offload available" if gpu
            else "installed wheel is CPU-only; see the README for the CUDA wheel",
        )
    except ImportError:
        row("llama-cpp", False, "llama-cpp-python is not installed")

    data_dir = settings.resolved_data_dir()
    row("data directory", data_dir.is_dir(), str(data_dir))

    try:
        from app.data.catalog import get_catalog

        catalog = get_catalog()
        row("datasets", not catalog.is_empty(),
            f"{len(catalog)} tables across {len(catalog.categories)} categories")
    except Exception as exc:
        row("datasets", False, str(exc))

    try:
        from app.store import get_store

        store = get_store()
        if store is None:
            table.add_row("chat store", "[yellow]off[/yellow]",
                          "PERSIST_CHATS=false; chats end with the process")
        else:
            counts = store.stats()
            row("chat store", True,
                f"{store.path} ({counts['chats']} chats, {counts['turns']} turns, "
                f"{counts['memories']} preferences, {counts['bytes'] / 1e3:.0f} KB)")
    except Exception as exc:
        row("chat store", False, str(exc))

    console.print(table)

    try:
        from app.llm import get_backend

        console.print(f"\nActive backend: [bold]{get_backend().info().describe()}[/bold]")
    except Exception as exc:
        console.print(f"\n[red]No usable backend:[/red] {exc}")


@application.command()
def datasets() -> None:
    """List the discovered tables."""
    configure_logging()
    from app.data.catalog import get_catalog

    catalog = get_catalog()
    if catalog.is_empty():
        console.print(f"[red]No datasets under {catalog.root}[/red]")
        raise typer.Exit(1)

    table = Table(show_header=True, header_style="bold")
    table.add_column("Table")
    table.add_column("Rows", justify="right")
    table.add_column("Columns", overflow="fold")

    for key in sorted(catalog.entries):
        entry = catalog.entries[key]
        profile = entry.profile
        table.add_row(key, f"{profile.rows:,}", ", ".join(profile.column_names))

    console.print(table)
    console.print(f"\nRoot: {catalog.root}")


@application.command()
def ask(
    question: str = typer.Argument(..., help="The question to answer."),
    theme: Optional[str] = typer.Option(None, help="Chart theme: light or dark."),
    no_cache: bool = typer.Option(False, "--no-cache", help="Bypass the response cache."),
    user: Optional[str] = typer.Option(None, "--user", "-u", help="Whose preferences to apply."),
) -> None:
    """Answer a single question.

    Standing preferences apply, but the question is not filed into any chat --
    one-off questions should not clutter the history that `chats` lists.
    """
    configure_logging()
    from app.agent import Analyst
    from app.session import UserSession

    session = UserSession(username=user)
    analyst = Analyst(conversation=session.conversation, use_cache=not no_cache)
    with console.status("Thinking...") as status:
        answer = analyst.ask(question, theme=theme,
                             on_progress=lambda stage: status.update(f"{stage}..."))
    _render(answer)
    if not answer.ok:
        raise typer.Exit(1)


@application.command()
def chat(
    theme: Optional[str] = typer.Option(None, help="Chart theme: light or dark."),
    user: Optional[str] = typer.Option(None, "--user", "-u", help="Whose chats to open."),
    chat_id: Optional[int] = typer.Option(None, "--chat", "-c", help="Resume this chat id."),
    new: bool = typer.Option(False, "--new", "-n", help="Start a new chat instead of resuming."),
) -> None:
    """Interactive session that remembers the conversation.

    Resumes the most recent chat by default, so closing the terminal is not the
    same as losing the thread.
    """
    configure_logging()
    from app.agent import Analyst
    from app.session import UserSession

    session = UserSession(username=user)

    if new:
        session.new_chat()
    elif chat_id is not None:
        if not session.open_chat(chat_id):
            console.print(f"[red]No chat {chat_id} for user '{session.username}'.[/red] "
                          "Run [bold]chats[/bold] to see what is there.")
            raise typer.Exit(1)
    else:
        session.ensure_chat()

    analyst = Analyst(conversation=session.conversation)
    _print_chat_banner(session)

    while True:
        try:
            entered = console.input("\n[bold cyan]>[/bold cyan] ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print()
            break

        if not entered:
            continue
        if entered.lower() in {"exit", "quit"}:
            break
        if entered.startswith("/") or entered.lower() == "reset":
            if _handle_command(entered, session, analyst):
                break
            continue

        # Titles the chat from its first question, before the answer exists, so
        # a question that goes on to fail still names the chat it happened in.
        session.note_question(entered)

        with console.status("Thinking...") as status:
            answer = analyst.ask(entered, theme=theme,
                                 on_progress=lambda stage: status.update(f"{stage}..."))
        _render(answer)


def _print_chat_banner(session) -> None:
    lines = ["Ask about your data.", ""]
    lines.append("[bold]exit[/bold] quit · [bold]reset[/bold] clear this chat")
    if session.persistent:
        lines.append("[bold]/new[/bold] · [bold]/chats[/bold] · [bold]/open <id>[/bold] "
                     "· [bold]/rename <title>[/bold]")
        lines.append("[bold]/remember key = value[/bold] · [bold]/memories[/bold] "
                     "· [bold]/forget <key>[/bold]")
    else:
        lines.append("[dim]persistence is off; this chat ends with the process[/dim]")

    title = f"AI Analyzer V3 — {session.username}"
    if session.chat_id is not None:
        title += f" · chat {session.chat_id}"
    console.print(Panel.fit("\n".join(lines), title=title))

    if session.conversation.turns:
        console.print(f"[dim]resumed {len(session.conversation.turns)} earlier "
                      f"turn(s)[/dim]")
    if session.conversation.memories:
        facts = ", ".join(f"{k}: {v}" for k, v in session.conversation.memories)
        console.print(f"[dim]remembering — {facts}[/dim]")


def _handle_command(entered: str, session, analyst) -> bool:
    """Run a slash command. Returns True when the loop should exit.

    `analyst` is rebound to whatever conversation the command leaves active, so
    switching chats mid-session actually changes what the agent can see.
    """
    body = entered[1:] if entered.startswith("/") else entered
    name, _, argument = body.partition(" ")
    name, argument = name.lower().strip(), argument.strip()

    if name in {"exit", "quit"}:
        return True

    if name == "reset":
        session.conversation.clear()
        console.print("[dim]conversation cleared[/dim]")
        return False

    if not session.persistent and name in {
        "new", "chats", "open", "rename", "remember", "memories", "forget"
    }:
        console.print("[yellow]Persistence is off, so there are no stored chats "
                      "or preferences.[/yellow] Set PERSIST_CHATS=true in .env.")
        return False

    if name == "new":
        session.new_chat()
        analyst.conversation = session.conversation
        console.print(f"[dim]started chat {session.chat_id}[/dim]")

    elif name == "chats":
        _print_chats(session)

    elif name == "open":
        if not argument.isdigit():
            console.print("[yellow]Usage: /open <id>[/yellow]")
        elif session.open_chat(int(argument)):
            analyst.conversation = session.conversation
            console.print(f"[dim]opened chat {session.chat_id} "
                          f"({len(session.conversation.turns)} turn(s))[/dim]")
        else:
            console.print(f"[red]No chat {argument} for '{session.username}'.[/red]")

    elif name == "rename":
        if not argument:
            console.print("[yellow]Usage: /rename <title>[/yellow]")
        else:
            session.rename_chat(argument)
            console.print(f"[dim]renamed to '{argument}'[/dim]")

    elif name == "remember":
        key, separator, value = argument.partition("=")
        if not separator or not key.strip() or not value.strip():
            console.print("[yellow]Usage: /remember key = value[/yellow]  "
                          "e.g. /remember region = Karnataka")
        else:
            session.remember(key.strip(), value.strip())
            console.print(f"[dim]remembered {key.strip()}: {value.strip()}[/dim]")

    elif name == "memories":
        _print_memories(session)

    elif name == "forget":
        if not argument:
            console.print("[yellow]Usage: /forget <key>  (or /forget --all)[/yellow]")
        elif argument in {"--all", "all"}:
            console.print(f"[dim]forgot {session.forget_all()} preference(s)[/dim]")
        else:
            session.forget(argument)
            console.print(f"[dim]forgot '{argument}'[/dim]")

    else:
        console.print(f"[yellow]Unknown command '/{name}'.[/yellow] "
                      "Try /chats, /new, /open, /rename, /remember, /memories, /forget.")

    return False


def _print_chats(session) -> None:
    entries = session.chats()
    if not entries:
        console.print("[dim]no chats yet[/dim]")
        return

    table = Table(show_header=True, header_style="bold")
    table.add_column("Id", justify="right")
    table.add_column("Title", overflow="fold")
    table.add_column("Turns", justify="right")
    table.add_column("Updated")
    for entry in entries:
        marker = " [cyan]<[/cyan]" if entry.id == session.chat_id else ""
        table.add_row(str(entry.id), entry.title + marker,
                      str(entry.turn_count), entry.updated_display)
    console.print(table)


def _print_memories(session) -> None:
    entries = session.memories()
    if not entries:
        console.print("[dim]nothing remembered yet — /remember key = value[/dim]")
        return

    table = Table(show_header=True, header_style="bold")
    table.add_column("Key")
    table.add_column("Value", overflow="fold")
    for entry in entries:
        table.add_row(entry.key, entry.value)
    console.print(table)


@application.command()
def chats(
    user: Optional[str] = typer.Option(None, "--user", "-u", help="Whose chats to list."),
) -> None:
    """List stored chats, newest first."""
    configure_logging()
    from app.session import UserSession

    session = UserSession(username=user)
    if not session.persistent:
        console.print("[yellow]Persistence is off (PERSIST_CHATS=false).[/yellow]")
        raise typer.Exit(1)
    _print_chats(session)


@application.command()
def remember(
    key: str = typer.Argument(..., help="What the preference is called, e.g. 'region'."),
    value: str = typer.Argument(..., help="Its value, e.g. 'Karnataka'."),
    user: Optional[str] = typer.Option(None, "--user", "-u", help="Whose preference this is."),
) -> None:
    """Store a standing preference, applied to every future question."""
    configure_logging()
    from app.session import UserSession

    session = UserSession(username=user)
    if not session.persistent:
        console.print("[yellow]Persistence is off (PERSIST_CHATS=false).[/yellow]")
        raise typer.Exit(1)
    session.remember(key, value)
    console.print(f"Remembered [bold]{key}[/bold]: {value}")


@application.command()
def memories(
    user: Optional[str] = typer.Option(None, "--user", "-u", help="Whose preferences to list."),
) -> None:
    """List the standing preferences applied to every question."""
    configure_logging()
    from app.session import UserSession

    _print_memories(UserSession(username=user))


@application.command()
def forget(
    key: Optional[str] = typer.Argument(None, help="The preference to remove."),
    all_of_them: bool = typer.Option(False, "--all", help="Remove every preference."),
    user: Optional[str] = typer.Option(None, "--user", "-u", help="Whose preference to remove."),
) -> None:
    """Remove a standing preference."""
    configure_logging()
    from app.session import UserSession

    session = UserSession(username=user)
    if not session.persistent:
        console.print("[yellow]Persistence is off (PERSIST_CHATS=false).[/yellow]")
        raise typer.Exit(1)

    if all_of_them:
        console.print(f"Forgot {session.forget_all()} preference(s).")
    elif key:
        session.forget(key)
        console.print(f"Forgot [bold]{key}[/bold].")
    else:
        console.print("[yellow]Name a preference, or pass --all.[/yellow]")
        raise typer.Exit(1)


@application.command("clear-cache")
def clear_cache() -> None:
    """Delete cached model responses."""
    configure_logging()
    console.print(f"Removed {cache.clear()['removed']} cached responses.")


def _render(answer) -> None:
    if not answer.ok:
        console.print(f"[red]Error:[/red] {answer.error}")
        return

    if answer.plan:
        console.print(f"[dim]{answer.plan}[/dim]")

    result = answer.result
    if result is not None and not result.is_empty:
        console.print()
        console.print(_result_table(result))

    if answer.narrative:
        console.print()
        console.print(Markdown(answer.narrative))

    if getattr(answer, "interpretation", ""):
        console.print()
        console.print("[bold]What this means[/bold]")
        console.print(Markdown(answer.interpretation))

    if answer.insights:
        console.print()
        console.print("[dim]Findings these are based on:[/dim]")
        for insight in answer.insights:
            colour = "yellow" if insight.severity == "caution" else "cyan"
            console.print(f"[{colour}]•[/{colour}] {insight.text}")

    if answer.followups:
        console.print()
        console.print("[dim]Ask next:[/dim]")
        for item in answer.followups:
            console.print(f"  [dim]›[/dim] {item.question}")

    if answer.notes:
        console.print()
        for note in answer.notes:
            console.print(f"[yellow]note:[/yellow] {note}")

    if answer.chart_path:
        console.print(f"\n[green]chart:[/green] {answer.chart_path}")

    console.print(f"[dim]{answer.elapsed:.1f}s | "
                  + " | ".join(f"{k} {v:.1f}s" for k, v in answer.timings.items())
                  + "[/dim]")


def _result_table(result) -> Table:
    frame = result.frame.head(settings.max_rows_in_prompt)
    table = Table(show_header=True, header_style="bold")
    for column in frame.columns:
        justify = "right" if column in result.value_columns else "left"
        table.add_column(str(column), justify=justify)

    # Reuse the executor's formatter: an `isinstance(value, float)` check misses
    # numpy's float32 (dtype downcasting produces it), and those fell through to
    # str() and printed as 4.001373e+06.
    from app.analysis.executor import _format_cell

    for _, record in frame.iterrows():
        table.add_row(*[_format_cell(record[column]) for column in frame.columns])
    return table


def main() -> None:
    # Make `python app/cli.py` work as well as `python -m app.cli`.
    root = str(Path(__file__).resolve().parents[1])
    if root not in sys.path:
        sys.path.insert(0, root)
    application()


if __name__ == "__main__":
    main()

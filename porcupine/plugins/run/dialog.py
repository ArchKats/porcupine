from __future__ import annotations

import sys
import tkinter
from tkinter import ttk
from typing import Callable, TypeVar

from porcupine import get_main_window, textutils, utils

from . import common, history

T = TypeVar("T")


class _FormattingEntryAndLabels:
    def __init__(
        self,
        entry_area: ttk.Frame,
        text: str,
        get_substituted_value: Callable[[], str],
        validated_callback: Callable[[], None],
    ):
        self._validated_callback = validated_callback
        self._get_substituted_value = get_substituted_value

        grid_y = entry_area.grid_size()[1]
        ttk.Label(entry_area, text=text).grid(row=grid_y, column=0, sticky="w")

        self.format_var = tkinter.StringVar()
        self.entry = ttk.Entry(entry_area, font="TkFixedFont", textvariable=self.format_var)
        self.entry.grid(row=grid_y, column=1, sticky="we", padx=(5, 0))
        self.entry.selection_range(0, "end")

        grid_y += 1
        self._command_display = textutils.create_passive_text_widget(
            entry_area, width=1, height=2, wrap="char", cursor="arrow"
        )
        self._command_display.grid(row=grid_y, column=1, sticky="we")

        self.format_var.trace_add("write", self.validate)

    def validate(self, *junk_from_var_trace: object) -> None:
        try:
            value = self._get_substituted_value()
        except (ValueError, KeyError, IndexError):
            self._command_display.config(state="normal", font="TkDefaultFont")
            self._command_display.delete("1.0", "end")
            self._command_display.insert("1.0", "Substitution error")
            self._command_display.config(state="disabled")
        else:
            self._command_display.config(state="normal", font="TkFixedFont")
            self._command_display.delete("1.0", "end")
            self._command_display.insert("1.0", value)
            self._command_display.config(state="disabled")

        self._validated_callback()


class _CommandAsker:
    def __init__(self, ctx: common.Context):
        self.window = tkinter.Toplevel(name="run_command_asker")

        if sys.platform == "win32":
            terminal_name = "command prompt"
        else:
            terminal_name = "terminal"

        content_frame = ttk.Frame(self.window, borderwidth=10)
        content_frame.pack(fill="both", expand=True)

        entry_area = ttk.Frame(content_frame)
        entry_area.pack(fill="x")
        entry_area.grid_columnconfigure(1, weight=1)

        self._substitutions = ctx.get_substitutions()
        self.command = _FormattingEntryAndLabels(
            entry_area,
            text="Run this command:",
            get_substituted_value=(lambda: self.get_command().format_command()),
            validated_callback=self.update_run_button,
        )
        self.cwd = _FormattingEntryAndLabels(
            entry_area,
            text="In this directory:",
            get_substituted_value=(lambda: str(self.get_command().format_cwd())),
            validated_callback=self.update_run_button,
        )

        ttk.Label(content_frame, text="Substitutions:").pack(anchor="w")

        sub_text = "\n".join("{%s} = %s" % pair for pair in self._substitutions.items())
        sub_textbox = textutils.create_passive_text_widget(
            content_frame, height=len(self._substitutions), width=1, wrap="none"
        )
        sub_textbox.pack(fill="x", padx=(15, 0), pady=(0, 20))
        sub_textbox.config(state="normal")
        sub_textbox.insert("1.0", sub_text)
        sub_textbox.config(state="disabled")

        porcupine_text = (
            "Display the output inside the Porcupine window (does not support keyboard input)"
        )
        external_text = f"Use an external {terminal_name} window"

        self.terminal_var = tkinter.BooleanVar()
        ttk.Radiobutton(
            content_frame,
            variable=self.terminal_var,
            value=False,
            text=porcupine_text,
            underline=porcupine_text.index("Porcupine"),
        ).pack(fill="x")
        ttk.Radiobutton(
            content_frame,
            variable=self.terminal_var,
            value=True,
            text=external_text,
            underline=external_text.index("external"),
        ).pack(fill="x")
        self.window.bind("<Alt-p>", (lambda e: self.terminal_var.set(False)), add=True)
        self.window.bind("<Alt-e>", (lambda e: self.terminal_var.set(True)), add=True)

        self._repeat_bindings = [
            utils.get_binding(f"<<Run:Repeat{key_id}>>") for key_id in range(4)
        ]
        self._repeat_var = tkinter.StringVar(value=self._repeat_bindings[ctx.key_id])
        self._repeat_var.trace_add("write", self.update_run_button)

        repeat_frame = ttk.Frame(content_frame)
        repeat_frame.pack(fill="x", pady=10)
        ttk.Label(
            repeat_frame, text="This command can be repeated by pressing the following key:"
        ).pack(side="left")
        ttk.Combobox(
            repeat_frame, textvariable=self._repeat_var, values=self._repeat_bindings, width=3
        ).pack(side="left")

        button_frame = ttk.Frame(content_frame)
        button_frame.pack(fill="x")
        cancel_button = ttk.Button(
            button_frame, text="Cancel", command=self.window.destroy, width=1
        )
        cancel_button.pack(side="left", fill="x", expand=True, padx=(0, 5))
        self.run_button = ttk.Button(button_frame, text="Run", command=self.on_run_clicked, width=1)
        self.run_button.pack(side="left", fill="x", expand=True, padx=(5, 0))
        self.run_clicked = False

        for entry in [self.command.entry, self.cwd.entry]:
            entry.bind("<Return>", (lambda e: self.run_button.invoke()), add=True)
            entry.bind("<Escape>", (lambda e: self.window.destroy()), add=True)

        previous_command = history.get_command_to_repeat(ctx)
        if previous_command is None:
            self.cwd.format_var.set("{folder_path}")
        else:
            self._select_command_autocompletion(previous_command, prefix="")

        # Autocomplete when pressing any key without alt
        self._suggestions = history.get_commands_to_suggest(ctx)
        self.command.entry.bind("<Key>", self._autocomplete, add=True)
        self.command.entry.bind("<Alt-Key>", (lambda e: None), add=True)

        self.command.entry.selection_range(0, "end")
        self.command.entry.focus_set()

        self.command.validate()
        self.cwd.validate()

    def get_command(self) -> common.Command:
        return common.Command(
            command_format=self.command.format_var.get(),
            cwd_format=self.cwd.format_var.get(),
            external_terminal=self.terminal_var.get(),
            substitutions=self._substitutions,
        )

    def get_key_id(self) -> int:
        return self._repeat_bindings.index(self._repeat_var.get())

    def command_and_cwd_are_valid(self) -> bool:
        try:
            command = self.get_command().format_command()
            cwd = self.get_command().format_cwd()
        except (ValueError, KeyError, IndexError):
            return False
        return bool(command.strip()) and cwd.is_dir() and cwd.is_absolute()

    def _select_command_autocompletion(self, command: common.Command, prefix: str) -> None:
        assert command.command_format.startswith(prefix)
        self.command.format_var.set(command.command_format)
        self.command.entry.icursor(len(prefix))
        self.command.entry.selection_range("insert", "end")
        self.cwd.format_var.set(command.cwd_format)
        self.terminal_var.set(command.external_terminal)

    def _autocomplete(self, event: tkinter.Event[tkinter.Entry]) -> str | None:
        if len(event.char) != 1 or not event.char.isprintable():
            return None

        text_to_keep = self.command.entry.get()
        if self.command.entry.selection_present():
            if self.command.entry.index("sel.last") != self.command.entry.index("end"):
                return None
            text_to_keep = text_to_keep[: self.command.entry.index("sel.first")]

        for item in self._suggestions:
            if item.command_format.startswith(text_to_keep + event.char):
                self._select_command_autocompletion(item, text_to_keep + event.char)
                return "break"

        return None

    def update_run_button(self, *junk: object) -> None:
        if self.command_and_cwd_are_valid() and self._repeat_var.get() in self._repeat_bindings:
            self.run_button.config(state="normal")
        else:
            self.run_button.config(state="disabled")

    def on_run_clicked(self) -> None:
        self.run_clicked = True
        self.window.destroy()


def ask_command(ctx: common.Context) -> tuple[common.Command, int] | None:
    if get_main_window().tk.call("winfo", "exists", ".run_command_asker"):
        get_main_window().tk.call("focus", ".run_command_asker")
        return None

    asker = _CommandAsker(ctx)
    asker.window.title("Run command")
    asker.window.transient(get_main_window())

    # you probably don't wanna resize it in y, it's safe to do it here,
    # as the content is already packed
    asker.window.resizable(True, False)
    asker.window.wait_window()

    if asker.run_clicked:
        return (asker.get_command(), asker.get_key_id())
    return None

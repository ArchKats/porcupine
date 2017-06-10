"""Handy utility functions and classes."""

import base64
import contextlib
import functools
import io
import itertools
import logging
import os
import pkgutil
import shutil
import sys
import threading
import tkinter as tk
import traceback

from porcupine import dirs

log = logging.getLogger(__name__)


class CallbackHook:
    """Simple object that runs callbacks.

    >>> hook = CallbackHook('whatever')
    >>> @hook.connect
    ... def user_callback(value):
    ...     print("user_callback called with", value)
    ...
    >>> hook.run(123)       # usually porcupine does this
    user_callback called with 123

    You can hook multiple callbacks too:

    >>> @hook.connect
    ... def another_callback(value):
    ...     print("another_callback called with", value)
    ...
    >>> hook.run(456)
    user_callback called with 456
    another_callback called with 456

    Errors in the connected functions will be logged to
    ``logging.getLogger(logname)``. The *unhandled_errors* argument
    should be an iterable of exceptions that won't be handled.
    """

    def __init__(self, logname, *, unhandled_errors=()):
        self._log = logging.getLogger(logname)
        self._unhandled = tuple(unhandled_errors)  # isinstance() likes tuples
        self.callbacks = []

    def connect(self, callback):
        """Schedule a function to be called when the hook is ran.

        This appends *callback* to :attr:`~callbacks`. The *callback* is
        also returned, so this can be used as a decorator.
        """
        self.callbacks.append(callback)
        return callback

    def disconnect(self, callback):
        """Remove *callback* from :attr:`~callbacks`."""
        self.callbacks.remove(callback)

    def _handle_error(self, callback, error):
        if isinstance(error, self._unhandled):
            raise error
        self._log.exception("%s doesn't work", nice_repr(callback))

    def run(self, *args):
        """Run ``callback(*args)`` for each connected callback."""
        for callback in self.callbacks:
            try:
                callback(*args)
            except Exception as e:
                self._handle_error(callback, e)


class ContextManagerHook(CallbackHook):
    """A :class:`.CallbackHook` subclass for "set up and tear down" callbacks.

    The connected callbacks should usually do something, yield and then
    undo everything they did, just like :func:`contextlib.contextmanager`
    functions.

    >>> hook = ContextManagerHook('whatever')
    >>> @hook.connect
    ... def hooked_callback():
    ...     print("setting up")
    ...     yield
    ...     print("tearing down")
    ...
    >>> with hook.run():
    ...     print("now things are set up")
    ...
    setting up
    now things are set up
    tearing down
    >>>
    """

    @contextlib.contextmanager
    def run(self, *args):
        """Run the ``callback(*args)`` generator for each connected callback.

        Use this as a context manager::

            with hook.run("the", "args", "go", "here"):
                ...
        """
        generators = []   # [(callback, generator), ...]
        for callback in self.callbacks:
            try:
                generator = callback(*args)
                if not hasattr(type(generator), '__next__'):
                    # it has no yields at all
                    raise RuntimeError("the function didn't yield")

                try:
                    next(generator)
                except StopIteration:
                    # it has a yield but it didn't run, e.g. if False: yield
                    raise RuntimeError("the function didn't yield")

                generators.append((callback, generator))

            except Exception as e:
                self._handle_error(callback, e)

        yield

        for callback, generator in generators:
            try:
                # next() should raise StopIteration, if it doesn't we
                # want to use self._handle_error and that's why the
                # raise is in the try block
                next(generator)
                raise RuntimeError("the function yielded twice")
            except StopIteration:
                pass
            except Exception as e:
                self._handle_error(callback, e)


# pythonw.exe sets sys.stdout to None because there's no console window,
# print still works because it checks if sys.stdout is None
running_pythonw = (sys.stdout is None)

# this is a hack :(
python = sys.executable
if running_pythonw and sys.executable.lower().endswith(r'\pythonw.exe'):
    # get rid of the 'w'
    _possible_python = sys.executable[:-5] + sys.executable[-4:]
    if os.path.isfile(_possible_python):
        python = _possible_python


def get_window(widget):
    """Return the ``tk.Tk`` or ``tk.Toplevel`` that *widget* is in."""
    while not isinstance(widget, (tk.Tk, tk.Toplevel)):
        widget = widget.master
    return widget


def get_root():
    """Return tkinter's current root window.

    Currently :class:`the editor object <porcupine.editor.Editor>` is
    always in the root window, but don't rely on that, it may change in
    the future.

    This function returns None if no tkinter root window has been
    created yet.
    """
    # tkinter's default root window is not accessible as a part of the
    # public API, but tkinter uses _default_root everywhere so I don't
    # think it's going away
    return tk._default_root


def copy_bindings(widget1, widget2):
    """Add all bindings of *widget1* to *widget2*.

    You should call ``copy_bindings(editor, focusable_widget)`` on all
    widgets that can be focused by e.g. clocking them, like ``Text`` and
    ``Entry`` widgets. This way porcupine's keyboard bindings will work
    with all widgets.
    """
    # tkinter's bind() can do quite a few different things depending
    # on how it's invoked
    for keysym in widget1.bind():
        tcl_command = widget1.bind(keysym)
        widget2.bind(keysym, tcl_command)


def bind_mouse_wheel(widget, callback, *, prefixes='', **bind_kwargs):
    """Bind mouse wheel events to callback.

    The callback will be called like ``callback(direction)`` where
    *direction* is ``'up'`` or ``'down'``. The *prefixes* argument can
    be used to change the binding string. For example,
    ``prefixes='Control-'`` means that callback will be ran when the
    user holds down Control and rolls the wheel.
    """
    # i needed to cheat and use stackoverflow, the man pages don't say
    # what OSX does with MouseWheel events and i don't have an
    # up-to-date OSX :( the non-x11 code should work on windows and osx
    # http://stackoverflow.com/a/17457843
    if get_root().tk.call('tk', 'windowingsystem') == 'x11':
        def real_callback(event):
            callback('up' if event.num == 4 else 'down')

        widget.bind('<{}Button-4>'.format(prefixes),
                    real_callback, **bind_kwargs)
        widget.bind('<{}Button-5>'.format(prefixes),
                    real_callback, **bind_kwargs)

    else:
        def real_callback(event):
            callback('up' if event.delta > 0 else 'down')

        widget.bind('<{}MouseWheel>'.format(prefixes),
                    real_callback, **bind_kwargs)


# FIXME: is lru_cache() guaranteed to hold references?
@functools.lru_cache()
def get_image(filename):
    """Create a ``tkinter.PhotoImage`` from a file in ``porcupine/images``.

    This function is cached and the cache holds references to all
    returned images, so there's no need to worry about calling this
    function too many times or keeping references to the returned
    images.
    """
    # only gif images should be added to porcupine/images, other image
    # formats don't work with old Tk versions
    data = pkgutil.get_data('porcupine', 'images/' + filename)
    return tk.PhotoImage(format='gif', data=base64.b64encode(data))


def errordialog(title, message, monospace_text=None):
    """This is a lot like ``tkinter.messagebox.showerror``.

    Don't rely on this, I'll probably move this to
    :mod:`porcupine.dialogs` later.

    This function can be called with or without creating a root window
    first. If *monospace_text* is not None, it will be displayed below
    the message in a ``tkinter.Text`` widget.

    Example::

        try:
            do something
        except SomeError:
            utils.errordialog("Oh no", "Doing something failed!",
                              traceback.format_exc())
    """
    root = get_root()
    if root is None:
        window = tk.Tk()
    else:
        window = tk.Toplevel()
        window.transient(root)

    label = tk.Label(window, text=message, height=5)

    if monospace_text is None:
        label.pack(fill='both', expand=True)
        geometry = '250x150'
    else:
        label.pack(anchor='center')
        text = tk.Text(window, width=1, height=1)
        text.pack(fill='both', expand=True)
        text.insert('1.0', monospace_text)
        text['state'] = 'disabled'
        geometry = '400x300'

    button = tk.Button(window, text="OK", width=6, command=window.destroy)
    button.pack(pady=10)

    window.title(title)
    window.geometry(geometry)
    window.wait_window()


def run_in_thread(blocking_function, done_callback):
    """Run ``blocking_function()`` in another thread.

    If the *blocking_function* raises an error,
    ``done_callback(False, traceback)`` will be called where *traceback*
    is the error message as a string. If no errors are raised,
    ``done_callback(True, result)`` will be called where *result* is the
    return value from *blocking_function*. The *done_callback* is always
    called from Tk's main loop, so it can do things with Tkinter widgets
    unlike *blocking_function*.
    """
    root = get_root()
    result = []     # [success, result]

    def thread_target():
        # the logging module uses locks so calling it from another
        # thread should be safe
        try:
            value = blocking_function()
            result[:] = [True, value]
        except Exception as e:
            result[:] = [False, traceback.format_exc()]

    def check():
        if thread.is_alive():
            # let's come back and check again later
            root.after(100, check)
        else:
            done_callback(*result)

    thread = threading.Thread(target=thread_target)
    thread.start()
    root.after_idle(check)


class Checkbox(tk.Checkbutton):
    """Like ``tkinter.Checkbutton``, but works with my dark GTK+ theme.

    Tkinter's Checkbutton displays a white checkmark on a white
    background on my dark GTK+ theme (BlackMATE on Mate 1.8). This class
    fixes that.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self['selectcolor'] == self['foreground'] == '#ffffff':
            self['selectcolor'] = self['background']


def nice_repr(obj):
    """Don't rely on this, this may be removed later.

    Return a nice string representation of an object.

    >>> import time
    >>> nice_repr(time.strftime)
    'time.strftime'
    >>> nice_repr(object())     # doctest: +ELLIPSIS
    '<object object at 0x...>'
    """
    try:
        return obj.__module__ + '.' + obj.__qualname__
    except AttributeError:
        return repr(obj)


@contextlib.contextmanager
def backup_open(path, *args, **kwargs):
    """Like :func:`open`, but uses a backup file if needed.

    This is useless with modes like ``'r'`` because they don't modify
    the file, but this is useful when overwriting the user's files.

    This needs to be used as a context manager. For example::

        try:
            with utils.backup_open(cool_file, 'w') as file:
                ...
        except (UnicodeError, OSError):
            # log the error and report it to the user

    This automatically restores from the backup on failure.
    """
    if os.path.exists(path):
        # there's something to back up
        name, ext = os.path.splitext(path)
        while os.path.exists(name + ext):
            name += '-backup'
        backuppath = name + ext

        log.info("backing up '%s' to '%s'", path, backuppath)
        shutil.copy(path, backuppath)

        try:
            yield open(path, *args, **kwargs)
        except Exception as e:
            log.info("restoring '%s' from the backup", path)
            shutil.move(backuppath, path)
            raise e
        else:
            log.info("deleting '%s'" % backuppath)
            os.remove(backuppath)

    else:
        yield open(path, *args, **kwargs)


if __name__ == '__main__':
    import doctest
    print(doctest.testmod())

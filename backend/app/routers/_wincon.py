"""Windows Pseudo Console (ConPTY) wrapper.

Provides a real pseudo-terminal on Windows 10 1809+ so that interactive shells
(cmd.exe / powershell) behave like on a real console: input is echoed, prompts
are shown, line editing works, and resize events are honored.

Implemented purely through ``kernel32.dll`` via :mod:`ctypes` so that no extra
third-party dependency is required. Raises :class:`ConPTYError` on
:meth:`ConPTY.start` if the API is unavailable or fails, so callers can fall
back to a plain pipe-based shell.
"""

from __future__ import annotations

import ctypes
import msvcrt
import os
from ctypes import wintypes

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

# --- constants --------------------------------------------------------------

PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE = 0x00020016
EXTENDED_STARTUPINFO_PRESENT = 0x00080000
STARTF_USESTDHANDLES = 0x00000100
STILL_ACTIVE = 259


# --- structures -------------------------------------------------------------

class COORD(ctypes.Structure):
    _fields_ = [("X", wintypes.SHORT), ("Y", wintypes.SHORT)]


class STARTUPINFOW(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("lpReserved", wintypes.LPWSTR),
        ("lpDesktop", wintypes.LPWSTR),
        ("lpTitle", wintypes.LPWSTR),
        ("dwX", wintypes.DWORD),
        ("dwY", wintypes.DWORD),
        ("dwXSize", wintypes.DWORD),
        ("dwYSize", wintypes.DWORD),
        ("dwXCountChars", wintypes.DWORD),
        ("dwYCountChars", wintypes.DWORD),
        ("dwFillAttribute", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("wShowWindow", wintypes.WORD),
        ("cbReserved2", wintypes.WORD),
        ("lpReserved2", ctypes.c_void_p),
        ("hStdInput", wintypes.HANDLE),
        ("hStdOutput", wintypes.HANDLE),
        ("hStdError", wintypes.HANDLE),
    ]


class STARTUPINFOEXW(ctypes.Structure):
    _fields_ = [("StartupInfo", STARTUPINFOW), ("lpAttributeList", ctypes.c_void_p)]


class PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("hProcess", wintypes.HANDLE),
        ("hThread", wintypes.HANDLE),
        ("dwProcessId", wintypes.DWORD),
        ("dwThreadId", wintypes.DWORD),
    ]


# --- function prototypes ----------------------------------------------------

kernel32.CreatePseudoConsole.argtypes = [
    COORD,
    wintypes.HANDLE,
    wintypes.HANDLE,
    wintypes.DWORD,
    ctypes.POINTER(wintypes.HANDLE),
]
kernel32.CreatePseudoConsole.restype = ctypes.c_long  # HRESULT

kernel32.ResizePseudoConsole.argtypes = [wintypes.HANDLE, COORD]
kernel32.ResizePseudoConsole.restype = ctypes.c_long  # HRESULT

kernel32.ClosePseudoConsole.argtypes = [wintypes.HANDLE]
kernel32.ClosePseudoConsole.restype = None

kernel32.InitializeProcThreadAttributeList.argtypes = [
    ctypes.c_void_p,
    wintypes.DWORD,
    wintypes.DWORD,
    ctypes.POINTER(ctypes.c_size_t),
]
kernel32.InitializeProcThreadAttributeList.restype = wintypes.BOOL

kernel32.UpdateProcThreadAttribute.argtypes = [
    ctypes.c_void_p,  # lpAttributeList
    wintypes.DWORD,  # dwFlags
    ctypes.c_void_p,  # Attribute (DWORD_PTR)
    ctypes.c_void_p,  # lpValue (for PSEUDOCONSOLE: the HPCON value itself)
    ctypes.c_size_t,  # cbSize
    ctypes.c_void_p,  # lpPreviousValue
    ctypes.c_void_p,  # lpReturnSize
]
kernel32.UpdateProcThreadAttribute.restype = wintypes.BOOL

kernel32.DeleteProcThreadAttributeList.argtypes = [ctypes.c_void_p]
kernel32.DeleteProcThreadAttributeList.restype = None

kernel32.CreatePipe.argtypes = [
    ctypes.POINTER(wintypes.HANDLE),
    ctypes.POINTER(wintypes.HANDLE),
    ctypes.c_void_p,  # lpPipeAttributes (NULL -> non-inheritable)
    wintypes.DWORD,
]
kernel32.CreatePipe.restype = wintypes.BOOL

kernel32.CreateProcessW.argtypes = [
    wintypes.LPCWSTR,
    wintypes.LPWSTR,
    ctypes.c_void_p,
    ctypes.c_void_p,
    wintypes.BOOL,
    wintypes.DWORD,
    ctypes.c_void_p,
    wintypes.LPCWSTR,
    ctypes.POINTER(STARTUPINFOEXW),
    ctypes.POINTER(PROCESS_INFORMATION),
]
kernel32.CreateProcessW.restype = wintypes.BOOL

kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.CloseHandle.restype = wintypes.BOOL

kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
kernel32.WaitForSingleObject.restype = wintypes.DWORD

kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
kernel32.TerminateProcess.restype = wintypes.BOOL

kernel32.GetExitCodeProcess.argtypes = [
    wintypes.HANDLE,
    ctypes.POINTER(wintypes.DWORD),
]
kernel32.GetExitCodeProcess.restype = wintypes.BOOL

kernel32.GetProcessHeap.restype = wintypes.HANDLE
kernel32.HeapAlloc.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.c_size_t]
kernel32.HeapAlloc.restype = ctypes.c_void_p
kernel32.HeapFree.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.c_void_p]
kernel32.HeapFree.restype = wintypes.BOOL


class ConPTYError(OSError):
    """Raised when the Pseudoconsole API is unavailable or fails."""


class ConPTY:
    """A Windows pseudo console hosting a child process (e.g. ``cmd.exe``).

    After :meth:`start`, read output via :meth:`read` (blocking) and feed
    input via :meth:`write`. Resize with :meth:`resize`. Call :meth:`close`
    to tear everything down.

    Note: the calling process should have a real console attached (not a
    redirected std handle) for the child to attach to the pseudoconsole
    correctly. Running under a normal console-attached process (e.g. uvicorn
    started in a terminal window) works as expected.
    """

    def __init__(self, rows: int = 24, cols: int = 80):
        self.rows = rows
        self.cols = cols
        self._hpc: int = 0
        self._h_process: int = 0
        self._h_thread: int = 0
        self.pid: int = 0
        self._in_write_fd = -1  # we write input here
        self._out_read_fd = -1  # we read output here
        self._attr_list = 0
        self._heap = 0

    def start(self, command: str) -> None:
        if not hasattr(kernel32, "CreatePseudoConsole"):
            raise ConPTYError("CreatePseudoConsole not available (need Windows 10 1809+)")

        # Pipes created non-inheritable (NULL sa), matching the official
        # MiniTerm sample. The pseudoconsole duplicates these handles internally.
        input_read = wintypes.HANDLE()
        input_write = wintypes.HANDLE()
        output_read = wintypes.HANDLE()
        output_write = wintypes.HANDLE()
        if not kernel32.CreatePipe(
            ctypes.byref(input_read), ctypes.byref(input_write), None, 0
        ):
            raise ConPTYError(f"CreatePipe(input) failed: {ctypes.WinError()}")
        if not kernel32.CreatePipe(
            ctypes.byref(output_read), ctypes.byref(output_write), None, 0
        ):
            kernel32.CloseHandle(input_read)
            kernel32.CloseHandle(input_write)
            raise ConPTYError(f"CreatePipe(output) failed: {ctypes.WinError()}")

        hpc = wintypes.HANDLE()
        hr = kernel32.CreatePseudoConsole(
            COORD(self.cols, self.rows), input_read, output_write, 0, ctypes.byref(hpc)
        )
        if hr != 0:
            kernel32.CloseHandle(input_read)
            kernel32.CloseHandle(input_write)
            kernel32.CloseHandle(output_read)
            kernel32.CloseHandle(output_write)
            raise ConPTYError(f"CreatePseudoConsole failed: 0x{hr & 0xFFFFFFFF:08X}")
        self._hpc = hpc.value

        # Build the proc-thread attribute list carrying the pseudoconsole.
        # HeapAlloc guarantees pointer-aligned memory (a ctypes byte array may
        # not be sufficiently aligned and silently breaks the attribute list).
        size_attr = ctypes.c_size_t(0)
        kernel32.InitializeProcThreadAttributeList(None, 1, 0, ctypes.byref(size_attr))
        heap = kernel32.GetProcessHeap()
        alist = kernel32.HeapAlloc(heap, 0, size_attr.value)
        if not alist:
            kernel32.ClosePseudoConsole(self._hpc)
            kernel32.CloseHandle(input_read)
            kernel32.CloseHandle(input_write)
            kernel32.CloseHandle(output_read)
            kernel32.CloseHandle(output_write)
            raise ConPTYError("HeapAlloc for attribute list failed")
        self._heap = heap
        self._attr_list = alist
        if not kernel32.InitializeProcThreadAttributeList(
            alist, 1, 0, ctypes.byref(size_attr)
        ):
            raise ConPTYError(
                f"InitializeProcThreadAttributeList failed: {ctypes.WinError()}"
            )
        # For PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE, lpValue is the HPCON value
        # itself (not a pointer to it) and cbSize is sizeof(HPCON) == pointer size.
        if not kernel32.UpdateProcThreadAttribute(
            alist,
            0,
            PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE,
            self._hpc,
            ctypes.sizeof(wintypes.HANDLE),
            None,
            None,
        ):
            raise ConPTYError(
                f"UpdateProcThreadAttribute failed: {ctypes.WinError()}"
            )

        si = STARTUPINFOEXW()
        si.StartupInfo.cb = ctypes.sizeof(STARTUPINFOEXW)
        # Set STARTF_USESTDHANDLES with NULL handles. Without this, when the
        # host process's std handles are redirected (e.g. uvicorn logging to a
        # pipe), the child inherits those pipe handles instead of attaching to
        # the pseudoconsole, and no input/output flows through the PTY. With
        # PSEUDOCONSOLE + STARTF_USESTDHANDLES(NULL), the system wires the
        # child's std handles to the pseudoconsole.
        si.StartupInfo.dwFlags = STARTF_USESTDHANDLES
        si.lpAttributeList = alist
        pi = PROCESS_INFORMATION()

        cmdline = ctypes.create_unicode_buffer(command)
        if not kernel32.CreateProcessW(
            None,
            cmdline,
            None,
            None,
            False,
            EXTENDED_STARTUPINFO_PRESENT,
            None,
            None,
            ctypes.byref(si),
            ctypes.byref(pi),
        ):
            err = ctypes.WinError()
            self._teardown()
            raise ConPTYError(f"CreateProcessW failed: {err}")

        self._h_process = pi.hProcess
        self._h_thread = pi.hThread
        self.pid = pi.dwProcessId

        # Drop the PTY-side handles now that the child is attached. The PTY
        # holds its own duplicates; closing ours lets I/O detect a broken
        # channel on teardown.
        kernel32.CloseHandle(input_read)
        kernel32.CloseHandle(output_write)

        # OS-level file descriptors for convenient os.read / os.write.
        self._out_read_fd = msvcrt.open_osfhandle(
            output_read.value, os.O_RDONLY | os.O_BINARY
        )
        self._in_write_fd = msvcrt.open_osfhandle(
            input_write.value, os.O_WRONLY | os.O_BINARY
        )
        if self._out_read_fd == -1 or self._in_write_fd == -1:
            self._teardown()
            raise ConPTYError("open_osfhandle failed")

    # -- I/O ----------------------------------------------------------------

    def read(self, size: int = 4096) -> bytes:
        if self._out_read_fd == -1:
            return b""
        try:
            return os.read(self._out_read_fd, size)
        except OSError:
            return b""

    def write(self, data: bytes) -> int:
        if self._in_write_fd == -1:
            return 0
        try:
            return os.write(self._in_write_fd, data)
        except OSError:
            return 0

    def resize(self, rows: int, cols: int) -> None:
        if not self._hpc:
            return
        self.rows, self.cols = rows, cols
        kernel32.ResizePseudoConsole(self._hpc, COORD(cols, rows))

    def is_alive(self) -> bool:
        if not self._h_process:
            return False
        code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(self._h_process, ctypes.byref(code)):
            return False
        return code.value == STILL_ACTIVE

    # -- cleanup ------------------------------------------------------------

    def _teardown(self) -> None:
        # Close the pseudoconsole FIRST. This breaks the internal pipe
        # connections and unblocks any thread stuck in os.read() on the
        # output fd. Closing the fd while a read is pending (the old
        # order) deadlocks on Windows because CloseHandle does not cancel
        # an outstanding synchronous read.
        if self._hpc:
            try:
                kernel32.ClosePseudoConsole(self._hpc)
            except Exception:
                pass
            self._hpc = 0
        if self._out_read_fd != -1:
            try:
                os.close(self._out_read_fd)
            except OSError:
                pass
            self._out_read_fd = -1
        if self._in_write_fd != -1:
            try:
                os.close(self._in_write_fd)
            except OSError:
                pass
            self._in_write_fd = -1
        if self._attr_list:
            alist = self._attr_list
            try:
                kernel32.DeleteProcThreadAttributeList(alist)
            except Exception:
                pass
            if self._heap:
                try:
                    kernel32.HeapFree(self._heap, 0, alist)
                except Exception:
                    pass
            self._attr_list = 0
        if self._h_thread:
            kernel32.CloseHandle(self._h_thread)
            self._h_thread = 0
        if self._h_process:
            kernel32.CloseHandle(self._h_process)
            self._h_process = 0

    def close(self) -> None:
        # Terminate the child first so ClosePseudoConsole (in _teardown)
        # doesn't block waiting for it to drain.
        if self._h_process and self.is_alive():
            try:
                kernel32.TerminateProcess(self._h_process, 0)
                kernel32.WaitForSingleObject(self._h_process, 2000)
            except Exception:
                pass
        self._teardown()

"""Manage only the installed ddhid63340 virtual device, never physical HID devices."""
from __future__ import annotations

# YH-ORIGIN: Yukki / ssyb | DEV-01 | publication trace; see NOTICE.md.
import ctypes
from ctypes import wintypes as w
import os
from pathlib import Path
import subprocess
import time
import winreg

from pydd import DDError

SERVICE = "ddhid63340"
HARDWARE_ID = r"ddhid63340\HID_DEVICE"
HID_CLASS = "{745a17a0-74d3-11d0-b6fe-00a0c90f57da}"
SERVICE_STOPPED, SERVICE_RUNNING = 1, 4
DN_STARTED, CM_PROB_DISABLED = 0x8, 22
CM_DISABLE_UI_NOT_OK, CM_DISABLE_PERSIST = 1, 2


class DeviceError(DDError):
    pass


class RestartRequired(DeviceError):
    """The persistent disable was accepted, but the driver remains loaded."""
    pass


class _ServiceStatus(ctypes.Structure):
    _fields_ = [(name, w.DWORD) for name in (
        "type", "state", "accepted", "win32_exit", "service_exit", "checkpoint", "wait_hint")]


def _signature(dll, name, result, *args):
    fn = getattr(dll, name)
    fn.restype, fn.argtypes = result, list(args)
    return fn


class WindowsDDDevice:
    def __init__(self):
        self.cm = ctypes.WinDLL("cfgmgr32", use_last_error=True)
        self.scm = ctypes.WinDLL("advapi32", use_last_error=True)
        _signature(self.cm, "CM_Locate_DevNodeW", w.ULONG, ctypes.POINTER(w.DWORD), w.LPWSTR, w.ULONG)
        _signature(self.cm, "CM_Get_DevNode_Status", w.ULONG,
                   ctypes.POINTER(w.ULONG), ctypes.POINTER(w.ULONG), w.DWORD, w.ULONG)
        _signature(self.cm, "CM_Enable_DevNode", w.ULONG, w.DWORD, w.ULONG)
        _signature(self.cm, "CM_Disable_DevNode", w.ULONG, w.DWORD, w.ULONG)
        _signature(self.scm, "OpenSCManagerW", w.HANDLE, w.LPCWSTR, w.LPCWSTR, w.DWORD)
        _signature(self.scm, "OpenServiceW", w.HANDLE, w.HANDLE, w.LPCWSTR, w.DWORD)
        _signature(self.scm, "QueryServiceStatus", w.BOOL, w.HANDLE, ctypes.POINTER(_ServiceStatus))
        _signature(self.scm, "CloseServiceHandle", w.BOOL, w.HANDLE)

    def validate(self, instance):
        # Yukki / ssyb [DEV-02]: verify DD identity before changing device state.
        # Instance numbers differ between computers. Verify the service, class,
        # and exact hardware ID before each mutation, not just a display name.
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            "SYSTEM\\CurrentControlSet\\Enum\\" + instance) as key:
            service = winreg.QueryValueEx(key, "Service")[0]
            hardware = winreg.QueryValueEx(key, "HardwareID")[0]
            device_class = winreg.QueryValueEx(key, "ClassGUID")[0]
        if (service.lower() != SERVICE or device_class.lower() != HID_CLASS
                or HARDWARE_ID.lower() not in [value.lower() for value in hardware]):
            raise DeviceError(f"设备身份不匹配，未操作：{instance}")

    def devices(self):
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                rf"SYSTEM\CurrentControlSet\Services\{SERVICE}\Enum") as key:
                count = winreg.QueryValueEx(key, "Count")[0]
                instances = [winreg.QueryValueEx(key, str(i))[0] for i in range(count)]
        except FileNotFoundError:
            return []
        for instance in instances:
            self.validate(instance)
        return instances

    def _node(self, instance):
        self.validate(instance)
        node = w.DWORD()
        self._check(self.cm.CM_Locate_DevNodeW(ctypes.byref(node), instance, 0), "查找设备")
        return node.value

    @staticmethod
    def _check(result, action):
        if result:
            detail = "，请以管理员身份运行" if result == 0x33 else ""
            raise DeviceError(f"{action}失败（CONFIGRET=0x{result:08X}）{detail}")

    def device_status(self, instance):
        status, problem = w.ULONG(), w.ULONG()
        self._check(self.cm.CM_Get_DevNode_Status(ctypes.byref(status), ctypes.byref(problem),
                                               self._node(instance), 0), "读取设备状态")
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            "SYSTEM\\CurrentControlSet\\Enum\\" + instance) as key:
            try:
                flags = winreg.QueryValueEx(key, "ConfigFlags")[0]
            except FileNotFoundError:
                flags = 0
        return {"instance": instance, "started": bool(status.value & DN_STARTED),
                "problem": problem.value, "configured_disabled": bool(flags & 1)}

    def service_state(self):
        manager = self.scm.OpenSCManagerW(None, None, 1)  # SC_MANAGER_CONNECT
        if not manager:
            raise ctypes.WinError(ctypes.get_last_error())
        service = None
        try:
            service = self.scm.OpenServiceW(manager, SERVICE, 4)  # SERVICE_QUERY_STATUS
            if not service:
                if ctypes.get_last_error() == 1060:
                    return SERVICE_STOPPED
                raise ctypes.WinError(ctypes.get_last_error())
            status = _ServiceStatus()
            if not self.scm.QueryServiceStatus(service, ctypes.byref(status)):
                raise ctypes.WinError(ctypes.get_last_error())
            return status.state
        finally:
            if service:
                self.scm.CloseServiceHandle(service)
            self.scm.CloseServiceHandle(manager)

    def snapshot(self):
        return {"service": SERVICE, "service_state": self.service_state(),
                "devices": [self.device_status(instance) for instance in self.devices()]}

    def enable(self, instance):
        self._check(self.cm.CM_Enable_DevNode(self._node(instance), 0), "启用 DD 设备")
        if self.device_status(instance)["configured_disabled"]:
            self._pnp_change("/enable-device", instance)

    def _pnp_change(self, action, instance, *, force=False):
        self.validate(instance)
        utility = Path(os.environ["SystemRoot"]) / "System32" / "pnputil.exe"
        command = [str(utility), action, instance]
        if force:
            command.append("/force")
        try:
            result = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, timeout=20,
                                    creationflags=subprocess.CREATE_NO_WINDOW)
        except subprocess.TimeoutExpired as exc:
            raise DeviceError(f"DD 设备设置超时（PnPUtil，{instance}），尚未确认设备状态。") from exc
        if result.returncode == 3010:
            raise RestartRequired("Windows 已保存 DD 设备设置，但要求重启才能完成；当前驱动未确认停止。")
        if result.returncode:
            detail = result.stdout.decode("mbcs", errors="replace").strip()
            raise DeviceError(f"DD 设备设置失败（PnPUtil={result.returncode}）：{detail}")

    def disable(self, instance):
        result = self.cm.CM_Disable_DevNode(
            self._node(instance), CM_DISABLE_UI_NOT_OK | CM_DISABLE_PERSIST)
        if result == 0x28:  # CR_NOT_DISABLEABLE: DD 63340 refuses normal disable.
            # Microsoft's supported PnP path, scoped to the verified DD device.
            # Never request a reboot, remove a driver package or touch real HID.
            self._pnp_change("/disable-device", instance, force=True)
        else:
            self._check(result, "禁用 DD 设备")


class DDDeviceController:
    def __init__(self, backend=None, *, timeout=8):
        self.backend = backend if backend is not None else WindowsDDDevice()
        self.timeout = timeout

    def _wait(self, predicate, message):
        deadline = time.monotonic() + self.timeout
        while True:
            snapshot = self.backend.snapshot()
            if predicate(snapshot):
                return snapshot
            if time.monotonic() >= deadline:
                raise DeviceError(f"{message}；当前状态：{snapshot}。可能需要重启系统。")
            time.sleep(.1)

    def start(self):
        instances = self.backend.devices()
        if not instances:
            raise DeviceError("未找到 ddhid63340 虚拟设备，请先安装匹配的 DD HID 63340 驱动。")
        for instance in instances:
            self.backend.enable(instance)
        return self._wait(lambda s: s["service_state"] == SERVICE_RUNNING and bool(s["devices"])
                          and all(d["started"] and not d["problem"] and not d.get("configured_disabled", False)
                                  for d in s["devices"]),
                          "DD 设备尚未启动")

    def stop(self):
        errors = []
        restart = False
        for instance in self.backend.devices():
            try:
                self.backend.disable(instance)
            except RestartRequired:
                restart = True
            except (OSError, DeviceError) as exc:
                errors.append(str(exc))
        if errors:
            raise DeviceError("；".join(errors))
        if restart:
            snapshot = self.backend.snapshot()
            if snapshot["service_state"] != SERVICE_STOPPED:
                raise RestartRequired("DD 设备已设置为禁用，但这版驱动无法立即停止，仍在系统中运行。请重启 Windows 完成停用。")
        # Successful API return alone is insufficient: verify both the PnP
        # device and the actual kernel driver service have stopped.
        return self._wait(lambda s: s["service_state"] == SERVICE_STOPPED
                          and all(not d["started"] and d["problem"] == CM_PROB_DISABLED
                                  for d in s["devices"]), "DD 内核驱动尚未停止")

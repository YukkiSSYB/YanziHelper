using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using System.Windows.Forms;

[assembly: DefaultDllImportSearchPaths(DllImportSearchPath.System32)]

namespace YanziDriverKit {
    internal interface IKit {
        bool NativeX64();
        IDisposable LockApp();
        bool Present();
        IDisposable LockDriver();
        int Install();
        void Wait();
        void StartApp();
    }

    internal static class Entry {
        internal static void Run(IKit kit) {
            if (!kit.NativeX64()) throw new InvalidOperationException("此驱动包只支持 Windows x64（AMD64）。");
            using (kit.LockApp()) {
                if (!kit.Present()) {
                    using (kit.LockDriver()) {
                        int code = kit.Install();
                        if (code == 3010 || code == 1641)
                            throw new InvalidOperationException("安装程序提示需要重新启动。请保存工作，手动重启后再双击启动助手。");
                        // The installer may return before PnP finishes publishing the device.
                        bool ready = kit.Present();
                        for (int i = 0; !ready && i < 20; ++i) { kit.Wait(); ready = kit.Present(); }
                        if (!ready) throw new InvalidOperationException("未检测到匹配的 DD HID 63340 设备，助手未启动。原厂安装程序退出码：" + code + "。请查看原厂安装结果；如提示需要重启，请手动重启后再试。");
                    }
                }
                kit.StartApp();
            }
        }

        [STAThread]
        static int Main() {
            try {
                using (new InstallGate()) Run(new WindowsKit(AppDomain.CurrentDomain.BaseDirectory));
                return 0;
            } catch (Exception err) {
                MessageBox.Show(err.Message, "启动助手", MessageBoxButtons.OK, MessageBoxIcon.Error);
                return 1;
            }
        }
    }

    internal sealed class InstallGate : IDisposable {
        readonly Mutex mutex;
        internal InstallGate() : this(@"Global\YanziHelper-DDHID63340-Setup-43c6c6e590994ea5") { }
        internal InstallGate(string name) {
            mutex = new Mutex(false, name);
            bool ours;
            try { ours = mutex.WaitOne(0); } catch (AbandonedMutexException) { ours = true; }
            if (!ours) {
                mutex.Dispose();
                throw new InvalidOperationException("另一个启动助手正在检测或安装设备，请等待该窗口完成。");
            }
        }
        public void Dispose() { try { mutex.ReleaseMutex(); } finally { mutex.Dispose(); } }
    }

    internal sealed class WindowsKit : IKit {
        const string AppName = "YanziHelper.exe";
        // build_launcher.ps1 supplies these values in a private, transient source file.
        internal static readonly Dictionary<string, string> DriverHashes =
            new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase) {
                {"ddc.exe", BuildHashes.Installer},
                {"ddhid63340.sys", BuildHashes.Driver},
                {"ddhid63340.inf", BuildHashes.Definition},
                {"ddhid63340.cat", BuildHashes.Catalog}
            };
        readonly string folder;
        string DriverFolder { get { return Path.Combine(folder, "driver"); } }
        internal WindowsKit(string folder) { this.folder = Path.GetFullPath(folder); }
        public bool NativeX64() { return Devices.NativeX64(); }
        public bool Present() { return Devices.Present(); }
        public void Wait() { Thread.Sleep(500); }

        internal static FileStream Verify(string path, string expected) {
            FileStream file = File.Open(path, FileMode.Open, FileAccess.Read, FileShare.Read);
            try {
                using (SHA256 hash = SHA256.Create()) {
                    string actual = BitConverter.ToString(hash.ComputeHash(file)).Replace("-", "");
                    if (!String.Equals(actual, expected, StringComparison.OrdinalIgnoreCase))
                        throw new InvalidDataException("文件校验失败：" + Path.GetFileName(path) + "。请从同一个发行 ZIP 重新完整解压。");
                }
                return file;
            } catch { file.Dispose(); throw; }
        }

        public IDisposable LockApp() { return Verify(Path.Combine(folder, AppName), BuildHashes.App); }
        sealed class OpenFiles : List<FileStream>, IDisposable {
            public void Dispose() { foreach (FileStream file in this) file.Dispose(); }
        }
        public IDisposable LockDriver() {
            return VerifyDriverFolder(DriverFolder);
        }
        internal static IDisposable VerifyDriverFolder(string path) {
            if (!Directory.Exists(path)) throw new DirectoryNotFoundException("缺少 driver 文件夹。请完整解压发行 ZIP 后再启动。");
            string[] entries = Directory.GetFileSystemEntries(path);
            if (entries.Length != DriverHashes.Count || entries.Any(p => !DriverHashes.ContainsKey(Path.GetFileName(p))
                || (File.GetAttributes(p) & (FileAttributes.Directory | FileAttributes.ReparsePoint)) != 0))
                throw new InvalidDataException("driver 文件夹的内容不符合本发行版，请重新完整解压。");
            OpenFiles files = new OpenFiles();
            try {
                foreach (var required in DriverHashes) files.Add(Verify(Path.Combine(path, required.Key), required.Value));
                return files;
            } catch { files.Dispose(); throw; }
        }
        internal static ProcessStartInfo Command(string folder, string file) {
            return new ProcessStartInfo { FileName = Path.Combine(folder, file), WorkingDirectory = folder,
                                          UseShellExecute = false, Arguments = "" };
        }
        public int Install() {
            using (Process proc = Process.Start(Command(DriverFolder, "ddc.exe"))) {
                proc.WaitForExit();
                return proc.ExitCode;
            }
        }
        public void StartApp() {
            using (Process proc = Process.Start(Command(folder, AppName))) { }
        }
    }

    internal static class Devices {
        static readonly Guid HidClass = new Guid("745a17a0-74d3-11d0-b6fe-00a0c90f57da");
        const string Service = "ddhid63340", Hardware = @"ddhid63340\HID_DEVICE";
        const uint PresentOnly = 2, HardwareIdProperty = 1, ServiceProperty = 4;
        const int End = 259, NoData = 13, BufferTooSmall = 122;

        [StructLayout(LayoutKind.Sequential)]
        struct DeviceInfo { public uint Size; public Guid ClassGuid; public uint DevInst; public IntPtr Reserved; }
        [StructLayout(LayoutKind.Sequential)]
        struct SystemInfo {
            public ushort Architecture, Reserved; public uint PageSize;
            public IntPtr MinimumAddress, MaximumAddress, ProcessorMask;
            public uint Processors, ProcessorType, AllocationGranularity;
            public ushort ProcessorLevel, ProcessorRevision;
        }
        [DllImport("kernel32.dll")] static extern void GetNativeSystemInfo(out SystemInfo info);
        [DllImport("kernel32.dll", SetLastError=true, ExactSpelling=true)]
        static extern bool IsWow64Process2(IntPtr process, out ushort processMachine, out ushort nativeMachine);
        internal static bool NativeX64() {
            try {
                ushort processMachine, nativeMachine;
                if (!IsWow64Process2(new IntPtr(-1), out processMachine, out nativeMachine))
                    throw new Win32Exception(Marshal.GetLastWin32Error());
                return nativeMachine == 0x8664;
            } catch (EntryPointNotFoundException) {
                SystemInfo info; GetNativeSystemInfo(out info); return info.Architecture == 9;
            }
        }
        [DllImport("setupapi.dll", CharSet=CharSet.Unicode, SetLastError=true, ExactSpelling=true)]
        static extern IntPtr SetupDiGetClassDevsW(ref Guid cls, string enumerator, IntPtr parent, uint flags);
        [DllImport("setupapi.dll", SetLastError=true, ExactSpelling=true)]
        static extern bool SetupDiEnumDeviceInfo(IntPtr devices, uint index, ref DeviceInfo info);
        [DllImport("setupapi.dll", SetLastError=true, ExactSpelling=true)]
        static extern bool SetupDiGetDeviceRegistryPropertyW(IntPtr devices, ref DeviceInfo info, uint property,
                out uint type, [Out] byte[] buffer, uint bufferSize, out uint required);
        [DllImport("setupapi.dll", ExactSpelling=true)]
        static extern bool SetupDiDestroyDeviceInfoList(IntPtr list);

        static byte[] Property(IntPtr devices, ref DeviceInfo info, uint id, uint expectedType) {
            uint required, kind;
            if (!SetupDiGetDeviceRegistryPropertyW(devices, ref info, id, out kind, null, 0, out required)) {
                int e = Marshal.GetLastWin32Error();
                if (e == NoData) return new byte[0];
                if (e != BufferTooSmall) throw new Win32Exception(e, "读取 HID 设备属性失败：" + e);
            }
            if (required == 0) return new byte[0];
            byte[] data = new byte[required];
            if (!SetupDiGetDeviceRegistryPropertyW(devices, ref info, id, out kind, data, (uint)data.Length, out required))
                throw new Win32Exception(Marshal.GetLastWin32Error());
            return kind == expectedType ? data : new byte[0];
        }

        internal static bool Match(Guid cls, string service, string[] hardware) {
            return cls == HidClass && String.Equals(service, Service, StringComparison.OrdinalIgnoreCase)
                && hardware.Any(h => String.Equals(h, Hardware, StringComparison.OrdinalIgnoreCase));
        }

        internal static bool Present() {
            Guid cls = HidClass;
            IntPtr set = SetupDiGetClassDevsW(ref cls, null, IntPtr.Zero, PresentOnly);
            if (set == new IntPtr(-1)) throw new Win32Exception(Marshal.GetLastWin32Error());
            try {
                for (uint index = 0;; ++index) {
                    DeviceInfo info = new DeviceInfo();
                    info.Size = (uint)Marshal.SizeOf(typeof(DeviceInfo));
                    if (!SetupDiEnumDeviceInfo(set, index, ref info)) {
                        int e = Marshal.GetLastWin32Error();
                        if (e == End) return false;
                        throw new Win32Exception(e, "枚举 HID 设备失败：" + e);
                    }
                    string service = Encoding.Unicode.GetString(Property(set, ref info, ServiceProperty, 1)).TrimEnd('\0');
                    if (!String.Equals(service, Service, StringComparison.OrdinalIgnoreCase)) continue;
                    string[] hardware = Encoding.Unicode.GetString(Property(set, ref info, HardwareIdProperty, 7))
                        .Split(new char[]{'\0'}, StringSplitOptions.RemoveEmptyEntries);
                    if (Match(info.ClassGuid, service, hardware)) return true;
                }
            } finally { SetupDiDestroyDeviceInfoList(set); }
        }
    }
}

using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Threading;
using YanziDriverKit;

internal static class TestLauncher {
    static int passed;
    sealed class Held : IDisposable {
        readonly Action done;
        public Held(Action done) { this.done = done; }
        public void Dispose() { done(); }
    }
    sealed class Fake : IKit {
        public bool X64 = true, DriverOK = true, AppOK = true, ProbeError;
        public int InstallerCode;
        public List<string> Trace = new List<string>();
        public Queue<bool> States;
        public Fake(params bool[] states) { States = new Queue<bool>(states); }
        public bool NativeX64() { Trace.Add("arch"); return X64; }
        public IDisposable LockApp() {
            Trace.Add("app-hash");
            if (!AppOK) throw new InvalidDataException("app hash");
            return new Held(delegate { Trace.Add("app-close"); });
        }
        public bool Present() {
            Trace.Add("probe");
            if (ProbeError) throw new IOException("unreadable device set");
            return States.Count > 0 && States.Dequeue();
        }
        public IDisposable LockDriver() {
            Trace.Add("driver-hash");
            if (!DriverOK) throw new InvalidDataException("driver hash");
            return new Held(delegate { Trace.Add("driver-close"); });
        }
        public int Install() { Trace.Add("install"); return InstallerCode; }
        public void Wait() { Trace.Add("wait"); }
        public void StartApp() { Trace.Add("start"); }
    }
    static void Check(bool condition, string name) {
        if (!condition) throw new Exception("FAIL " + name);
        Console.WriteLine("PASS " + name); ++passed;
    }
    static string Run(Fake fake) {
        try { Entry.Run(fake); return null; } catch (Exception e) { return e.Message; }
    }
    static void Main(string[] args) {
        if (args.Length != 0 && args.Length != 2) throw new Exception("optional args: release directory and vendor driver directory");
        Fake f = new Fake(true);
        Check(Run(f) == null && String.Join(",", f.Trace) == "arch,app-hash,probe,start,app-close", "present device skips installer");
        f = new Fake(false, true);
        Check(Run(f) == null && String.Join(",", f.Trace) == "arch,app-hash,probe,driver-hash,install,probe,driver-close,start,app-close", "absent device installs, rechecks, then starts");
        f = new Fake(false, false, true);
        Check(Run(f) == null && f.Trace.Count(t => t == "wait") == 1 && f.Trace.Contains("start"), "delayed enumeration");
        f = new Fake(false, false);
        Check(Run(f) != null && f.Trace.Count(t => t == "wait") == 20 && !f.Trace.Contains("start"), "missing after successful installer blocks app");
        f = new Fake(false, true) { InstallerCode = 9 };
        Check(Run(f) == null && f.Trace.Contains("start"), "undocumented exit and verified device");
        f = new Fake(false) { InstallerCode = 17 };
        Check(Run(f).Contains("17") && !f.Trace.Contains("start"), "undocumented exit and missing device");
        foreach (int code in new int[]{3010,1641}) {
            f = new Fake(false, true) { InstallerCode = code };
            Check(Run(f) != null && !f.Trace.Contains("start"), "restart result " + code);
        }
        f = new Fake(false) { X64 = false };
        Check(Run(f) != null && f.Trace.Count == 1, "unsupported architecture blocks everything");
        f = new Fake(true) { AppOK = false };
        Check(Run(f) != null && !f.Trace.Contains("probe") && !f.Trace.Contains("start"), "bad app blocks before device query");
        f = new Fake(false) { DriverOK = false };
        Check(Run(f) != null && !f.Trace.Contains("install") && !f.Trace.Contains("start"), "bad driver blocks execution");
        f = new Fake(false) { ProbeError = true };
        Check(Run(f) != null && !f.Trace.Contains("install"), "enumeration error does not install");
        Guid hid = new Guid("745a17a0-74d3-11d0-b6fe-00a0c90f57da");
        string[] ids = { "different", @"DDHID63340\HID_DEVICE" };
        Check(Devices.Match(hid, "DDHID63340", ids), "all device identifiers match case-insensitively");
        Check(!Devices.Match(Guid.Empty, "ddhid63340", ids) && !Devices.Match(hid, "other", ids)
            && !Devices.Match(hid, "ddhid63340", new string[]{@"ddhid63340\HID_DEVICE1"}), "reject wrong class, service or hardware id");
        bool denied = false;
        string testMutex = @"Local\YanziHelper-LauncherTest-" + Guid.NewGuid().ToString("N");
        using (new InstallGate(testMutex)) {
            Thread other = new Thread(delegate() {
                try { using (new InstallGate(testMutex)) { } } catch (InvalidOperationException) { denied = true; }
            });
            other.Start(); other.Join();
        }
        using (new InstallGate(testMutex)) Check(denied, "simultaneous launcher blocked; lock releases normally");
        if (args.Length == 2) {
            using (new WindowsKit(args[0]).LockApp()) Check(true, "release app hash verified read-only");
            using (WindowsKit.VerifyDriverFolder(args[1])) Check(true, "all four vendor files verified read-only");
            bool mismatch = false;
            try { using (WindowsKit.Verify(Path.Combine(args[1], "ddc.exe"), "00")) { } } catch (InvalidDataException) { mismatch = true; }
            Check(mismatch, "actual file mismatch rejected");
        }
        string driverFolder = args.Length == 2 ? args[1] : Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "driver");
        var command = WindowsKit.Command(driverFolder, "ddc.exe");
        Check(command.FileName == Path.Combine(driverFolder, "ddc.exe") && command.WorkingDirectory == driverFolder
            && command.Arguments == "" && !command.UseShellExecute, "exact installer, cwd and no optional arguments");
        Console.WriteLine("Passed " + passed + " checks; no real installer or helper was started.");
    }
}

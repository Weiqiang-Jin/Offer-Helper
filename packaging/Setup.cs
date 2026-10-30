using System;
using System.IO;
using System.IO.Compression;
using System.Diagnostics;
using System.Reflection;
using System.Threading.Tasks;
using System.Windows.Forms;
using System.Drawing;
using Microsoft.Win32;

// Per-user installer built with the .NET compiler included with Windows.
class Setup : Form {
    static readonly string Target = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "Programs", "OfferHelper");
    const string RegistryPath = @"Software\Microsoft\Windows\CurrentVersion\Uninstall\OfferHelper";
    Label message = new Label { AutoSize = false, Left = 24, Top = 24, Width = 500, Height = 85 };
    ProgressBar progress = new ProgressBar { Left = 24, Top = 112, Width = 500, Style = ProgressBarStyle.Marquee, Visible = false };
    Button install = new Button { Left = 390, Top = 155, Width = 134, Height = 34, Text = "安装" };
    bool running;

    [STAThread] static int Main(string[] args) {
        Application.EnableVisualStyles();
#if UNINSTALL
        return Uninstall();
#else
        if (Array.IndexOf(args, "/SILENT") >= 0) {
            try { Install(); return 0; }
            catch (Exception e) { File.WriteAllText(Path.Combine(Path.GetTempPath(), "OfferHelper-setup-error.txt"), e.ToString()); return 1; }
        }
        Application.Run(new Setup());
        return 0;
#endif
    }
    Setup() {
        Text = "安装 Offer Helper 0.3.0";
        ClientSize = new Size(550, 215);
        FormBorderStyle = FormBorderStyle.FixedDialog;
        MaximizeBox = false;
        StartPosition = FormStartPosition.CenterScreen;
        Icon = Icon.ExtractAssociatedIcon(Application.ExecutablePath);
        message.Text = "安装到当前用户目录，无需管理员权限。\n\n" + Target;
        Controls.Add(message); Controls.Add(progress); Controls.Add(install);
        FormClosing += delegate(object sender, FormClosingEventArgs e) { if (running) e.Cancel = true; };
        install.Click += async delegate {
            if (install.Text == "打开软件") {
                Process.Start(Path.Combine(Target, "OfferHelper.exe")); Close(); return;
            }
            running = true; install.Enabled = false; progress.Visible = true;
            message.Text = "正在安装，请稍候…";
            try {
                await Task.Run((Action)Install);
                message.Text = "安装完成。已创建猫咪图标的桌面快捷方式。\n\n首次使用可在“语音模型”菜单选择或下载模型。";
                install.Text = "打开软件";
            } catch (Exception e) {
                message.Text = "安装失败：" + e.Message;
            } finally { running = false; install.Enabled = true; progress.Visible = false; }
        };
    }
    static void EnsureStopped() {
        foreach (Process p in Process.GetProcessesByName("OfferHelper")) {
            try {
                if (String.Equals(p.MainModule.FileName, Path.Combine(Target, "OfferHelper.exe"), StringComparison.OrdinalIgnoreCase))
                    throw new InvalidOperationException("请先退出正在运行的 Offer Helper，再安装或卸载。");
            } catch (System.ComponentModel.Win32Exception) { }
        }
    }
    static void Install() {
        EnsureStopped();
        Directory.CreateDirectory(Target);
        using (Stream stream = Assembly.GetExecutingAssembly().GetManifestResourceStream("payload.zip")) {
            if (stream == null) throw new InvalidOperationException("安装包缺少文件。");
            using (ZipArchive zip = new ZipArchive(stream, ZipArchiveMode.Read)) {
                foreach (ZipArchiveEntry entry in zip.Entries) {
                    string path = Path.GetFullPath(Path.Combine(Target, entry.FullName));
                    if (!path.StartsWith(Target + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase))
                        throw new InvalidDataException("安装包包含无效路径。");
                    if (String.IsNullOrEmpty(entry.Name)) { Directory.CreateDirectory(path); continue; }
                    Directory.CreateDirectory(Path.GetDirectoryName(path));
                    entry.ExtractToFile(path, true);
                }
            }
        }
        Shortcut(Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory), "Offer Helper.lnk"));
        string menu = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.Programs), "Offer Helper");
        Directory.CreateDirectory(menu); Shortcut(Path.Combine(menu, "Offer Helper.lnk"));
        using (RegistryKey key = Registry.CurrentUser.CreateSubKey(RegistryPath)) {
            key.SetValue("DisplayName", "Offer Helper"); key.SetValue("DisplayVersion", "0.3.0");
            key.SetValue("Publisher", "Offer Helper"); key.SetValue("InstallLocation", Target);
            key.SetValue("DisplayIcon", Path.Combine(Target, "OfferHelper.exe"));
            key.SetValue("UninstallString", "\"" + Path.Combine(Target, "Uninstall.exe") + "\"");
            key.SetValue("NoModify", 1); key.SetValue("NoRepair", 1);
        }
    }
    static void Shortcut(string path) {
        dynamic shell = Activator.CreateInstance(Type.GetTypeFromProgID("WScript.Shell"));
        dynamic link = shell.CreateShortcut(path);
        link.TargetPath = Path.Combine(Target, "OfferHelper.exe");
        link.WorkingDirectory = Target; link.IconLocation = link.TargetPath + ",0";
        link.Description = "Offer Helper"; link.Save();
        System.Runtime.InteropServices.Marshal.FinalReleaseComObject(link);
        System.Runtime.InteropServices.Marshal.FinalReleaseComObject(shell);
    }
    static int Uninstall() {
        if (MessageBox.Show("卸载 Offer Helper？个人配置和模型将保留在用户数据目录。", "Offer Helper", MessageBoxButtons.YesNo) != DialogResult.Yes) return 0;
        try {
            EnsureStopped();
            string own = Path.GetFullPath(Application.ExecutablePath);
            if (!String.Equals(own, Path.Combine(Target, "Uninstall.exe"), StringComparison.OrdinalIgnoreCase))
                throw new InvalidOperationException("请从已安装的软件目录执行卸载。");
            // Only remove files belonging to this fixed per-user installation.
            foreach (string dir in Directory.GetDirectories(Target)) Directory.Delete(dir, true);
            foreach (string file in Directory.GetFiles(Target)) if (!String.Equals(file, own, StringComparison.OrdinalIgnoreCase)) File.Delete(file);
            File.Delete(Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory), "Offer Helper.lnk"));
            string menu = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.Programs), "Offer Helper", "Offer Helper.lnk");
            File.Delete(menu);
            Registry.CurrentUser.DeleteSubKeyTree(RegistryPath, false);
            MessageBox.Show("卸载完成。可在退出后删除安装目录中剩余的 Uninstall.exe。", "Offer Helper");
            return 0;
        } catch (Exception e) { MessageBox.Show(e.Message, "卸载失败"); return 1; }
    }
}

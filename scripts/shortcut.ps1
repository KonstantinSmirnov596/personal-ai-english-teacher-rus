# Windows shortcuts (.lnk) through the Unicode shell API (IShellLinkW). Dot-source it.
#
# WScript.Shell passes paths through the ANSI code page: with a folder such as "Jose N" written
# with accents it cannot save the shortcut, and the target it stores comes back mangled. The
# Unicode interface below works with any path. Windows PowerShell 5.1 compatible; keep it ASCII.

if (-not ('EnglishTrainer.Shortcut' -as [type])) {
    Add-Type -Language CSharp -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
using System.Runtime.InteropServices.ComTypes;
using System.Text;

namespace EnglishTrainer {
    [ComImport, Guid("00021401-0000-0000-C000-000000000046")]
    class ShellLinkObject {}

    [ComImport, InterfaceType(ComInterfaceType.InterfaceIsIUnknown),
     Guid("000214F9-0000-0000-C000-000000000046")]
    interface IShellLinkW {
        void GetPath([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder pszFile, int cch, IntPtr pfd, int fFlags);
        void GetIDList(out IntPtr ppidl);
        void SetIDList(IntPtr pidl);
        void GetDescription([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder pszName, int cch);
        void SetDescription([MarshalAs(UnmanagedType.LPWStr)] string pszName);
        void GetWorkingDirectory([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder pszDir, int cch);
        void SetWorkingDirectory([MarshalAs(UnmanagedType.LPWStr)] string pszDir);
        void GetArguments([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder pszArgs, int cch);
        void SetArguments([MarshalAs(UnmanagedType.LPWStr)] string pszArgs);
        void GetHotkey(out short pwHotkey);
        void SetHotkey(short wHotkey);
        void GetShowCmd(out int piShowCmd);
        void SetShowCmd(int iShowCmd);
        void GetIconLocation([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder pszIconPath, int cch, out int piIcon);
        void SetIconLocation([MarshalAs(UnmanagedType.LPWStr)] string pszIconPath, int iIcon);
        void SetRelativePath([MarshalAs(UnmanagedType.LPWStr)] string pszPathRel, int dwReserved);
        void Resolve(IntPtr hwnd, int fFlags);
        void SetPath([MarshalAs(UnmanagedType.LPWStr)] string pszFile);
    }

    public static class Shortcut {
        public static void Save(string lnk, string target, string args, string workDir, string icon, string description) {
            IShellLinkW link = (IShellLinkW)new ShellLinkObject();
            link.SetPath(target);
            link.SetArguments(args);
            link.SetWorkingDirectory(workDir);
            link.SetDescription(description);
            link.SetShowCmd(1);
            if (!String.IsNullOrEmpty(icon)) { link.SetIconLocation(icon, 0); }
            ((IPersistFile)link).Save(lnk, true);
        }

        public static string Target(string lnk) {
            IShellLinkW link = (IShellLinkW)new ShellLinkObject();
            ((IPersistFile)link).Load(lnk, 0);
            StringBuilder sb = new StringBuilder(2048);
            link.GetPath(sb, sb.Capacity, IntPtr.Zero, 0);
            return sb.ToString();
        }

        public static string Arguments(string lnk) {
            IShellLinkW link = (IShellLinkW)new ShellLinkObject();
            ((IPersistFile)link).Load(lnk, 0);
            StringBuilder sb = new StringBuilder(2048);
            link.GetArguments(sb, sb.Capacity);
            return sb.ToString();
        }
    }
}
'@
}

function New-TrShortcut([string]$Path, [string]$Target, [string]$Arguments, [string]$WorkDir, [string]$Icon) {
    [EnglishTrainer.Shortcut]::Save($Path, $Target, $Arguments, $WorkDir, $Icon, 'English Trainer')
}

function Get-TrShortcutTarget([string]$Path) {
    try { return [EnglishTrainer.Shortcut]::Target($Path) } catch { return '' }
}

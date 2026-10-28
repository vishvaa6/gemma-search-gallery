Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;

[ComImport, Guid("DC1C5A9C-E88A-4dde-A5A1-60F82A20AEF7")]
internal class FileOpenDialogRCW {}

[ComImport, Guid("42f85136-db7e-439c-85f1-e4075d135fc8"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
internal interface IFileOpenDialog {
    [PreserveSig] int Show([In] IntPtr parent);
    void SetFileTypes();
    void SetFileTypeIndex();
    void GetFileTypeIndex();
    void Advise();
    void Unadvise();
    void SetOptions([In] uint fos);
    void GetOptions(out uint pfos);
    void SetDefaultFolder(IShellItem psi);
    void SetFolder(IShellItem psi);
    void GetFolder(out IShellItem ppsi);
    void GetCurrentSelection(out IShellItem ppsi);
    void SetFileName([In, MarshalAs(UnmanagedType.LPWStr)] string pszName);
    void GetFileName([MarshalAs(UnmanagedType.LPWStr)] out string pszName);
    void SetTitle([In, MarshalAs(UnmanagedType.LPWStr)] string pszTitle);
    void SetOkButtonLabel([In, MarshalAs(UnmanagedType.LPWStr)] string pszText);
    void SetFileNameLabel([In, MarshalAs(UnmanagedType.LPWStr)] string pszLabel);
    void GetResult(out IShellItem ppsi);
    void AddPlace(IShellItem psi, int fdap);
    void SetDefaultExtension([In, MarshalAs(UnmanagedType.LPWStr)] string pszDefaultExtension);
    void Close(int hr);
    void SetClientGuid();
    void ClearClientData();
    void SetFilter();
}

[ComImport, Guid("43826D1E-E718-42EE-BC55-A1E261C37BFE"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
internal interface IShellItem {
    void BindToHandler();
    void GetParent();
    void GetDisplayName([In] uint sigdnName, [MarshalAs(UnmanagedType.LPWStr)] out string ppszName);
    void GetAttributes();
    void Compare();
}

public class ModernExplorerFolderPicker {
    [DllImport("user32.dll")]
    public static extern IntPtr GetForegroundWindow();

    [DllImport("user32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    public static extern bool SetForegroundWindow(IntPtr hWnd);

    private const uint FOS_PICKFOLDERS = 0x00000020;
    private const uint FOS_FORCEFILESYSTEM = 0x00000040;
    private const uint FOS_PATHMUSTEXIST = 0x00000800;
    private const uint FOS_DONTADDTORECENT = 0x02000000;
    private const uint SIGDN_FILESYSPATH = 0x80058000;

    public static string PickFolder(IntPtr ownerHandle) {
        var dialog = (IFileOpenDialog)(new FileOpenDialogRCW());
        try {
            uint options;
            dialog.GetOptions(out options);
            dialog.SetOptions(options | FOS_PICKFOLDERS | FOS_FORCEFILESYSTEM | FOS_PATHMUSTEXIST | FOS_DONTADDTORECENT);
            dialog.SetTitle("Select Media Folder to Inspect and Index");
            dialog.SetOkButtonLabel("Inspect Folder");
            dialog.SetFileNameLabel("Folder:");

            int hr = dialog.Show(ownerHandle);
            if (hr != 0) {
                return null;
            }

            IShellItem item;
            dialog.GetResult(out item);
            if (item == null) {
                return null;
            }

            string selectedPath;
            item.GetDisplayName(SIGDN_FILESYSPATH, out selectedPath);
            return selectedPath;
        } finally {
            Marshal.ReleaseComObject(dialog);
        }
    }
}
"@ -ErrorAction Stop

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

# Create an invisible zero-size topmost owner window so the modern Explorer dialog
# always opens in front of the browser without any visible background WinForms window.
[System.Reflection.Assembly]::LoadWithPartialName("System.Windows.Forms") | Out-Null
$ownerForm = New-Object System.Windows.Forms.Form
$ownerForm.TopMost = $true
$ownerForm.ShowInTaskbar = $false
$ownerForm.FormBorderStyle = [System.Windows.Forms.FormBorderStyle]::None
$ownerForm.StartPosition = [System.Windows.Forms.FormStartPosition]::Manual
$ownerForm.Location = New-Object System.Drawing.Point(-32000, -32000)
$ownerForm.Size = New-Object System.Drawing.Size(1, 1)
$ownerForm.Opacity = 0
$ownerForm.Show()
$ownerForm.Activate()
[ModernExplorerFolderPicker]::SetForegroundWindow($ownerForm.Handle) | Out-Null

try {
    $selected = [ModernExplorerFolderPicker]::PickFolder($ownerForm.Handle)
    if ($selected -and (Test-Path -LiteralPath $selected)) {
        Write-Output $selected
    } else {
        Write-Output "__CANCELLED__"
    }
} catch {
    Write-Output "__CANCELLED__"
} finally {
    $ownerForm.Close()
    $ownerForm.Dispose()
}

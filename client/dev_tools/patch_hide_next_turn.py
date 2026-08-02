"""
patch_hide_next_turn.py — Hide the Next Turn button in TestBed mode
===================================================================
Patches the dialog resource template to clear the WS_VISIBLE flag on the
Next Turn button, making it invisible while keeping Save and Load buttons
fully functional.

This is a 1-byte patch in the .rsrc section:
  File offset 0x7D6BD3: 0x50 → 0x40
  (clears WS_VISIBLE from the Next Turn button's window style)

The three buttons in the TestBed dialog:
  Next Turn  ID=0x0425  style 0x50010000 → 0x40010000 (hidden)
  Load       ID=0x0426  style 0x50010000 (unchanged)
  Save       ID=0x0483  style 0x50010000 (unchanged)

Usage:
    python patch_hide_next_turn.py                          # patches CosmicSupremacy.exe
    python patch_hide_next_turn.py CosmicSupremacy.exe      # same
    python patch_hide_next_turn.py --restore                # restores visibility
"""
import sys
import os
import shutil

PATCH_OFFSET = 0x7D6BD3
ORIGINAL_BYTE = 0x50  # WS_CHILD | WS_VISIBLE | BS_PUSHBUTTON
PATCHED_BYTE  = 0x40  # WS_CHILD | BS_PUSHBUTTON (no WS_VISIBLE)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


def main():
    restore = "--restore" in sys.argv
    args = [a for a in sys.argv[1:] if not a.startswith("--")]

    src = args[0] if args else os.path.join(SCRIPT_DIR, "CosmicSupremacy.exe")
    if not os.path.exists(src):
        print(f"ERROR: File not found: {src}")
        sys.exit(1)

    with open(src, "rb") as f:
        data = bytearray(f.read())

    current = data[PATCH_OFFSET]

    if restore:
        if current == ORIGINAL_BYTE:
            print(f"Next Turn button is already visible (0x{current:02X}).")
            return
        if current != PATCHED_BYTE:
            print(f"WARNING: Unexpected byte at 0x{PATCH_OFFSET:08X}: 0x{current:02X}")
            print(f"Expected 0x{PATCHED_BYTE:02X} (patched) — file may have other modifications.")
            resp = input("Continue anyway? (yes/no): ").strip().lower()
            if resp != "yes":
                print("Aborted.")
                sys.exit(0)
        data[PATCH_OFFSET] = ORIGINAL_BYTE
        action = "Restored"
        detail = "WS_VISIBLE set — Next Turn button is now visible"
    else:
        if current == PATCHED_BYTE:
            print(f"Next Turn button is already hidden (0x{current:02X}).")
            return
        if current != ORIGINAL_BYTE:
            print(f"WARNING: Unexpected byte at 0x{PATCH_OFFSET:08X}: 0x{current:02X}")
            print(f"Expected 0x{ORIGINAL_BYTE:02X} (original) — file may have other modifications.")
            resp = input("Continue anyway? (yes/no): ").strip().lower()
            if resp != "yes":
                print("Aborted.")
                sys.exit(0)
        data[PATCH_OFFSET] = PATCHED_BYTE
        action = "Patched"
        detail = "WS_VISIBLE cleared — Next Turn button is now hidden"

    # Backup
    backup = src + ".bak"
    if not os.path.exists(backup):
        shutil.copy2(src, backup)
        print(f"Backup: {backup}")

    with open(src, "wb") as f:
        f.write(data)

    print(f"{action}: {src}")
    print(f"  Offset 0x{PATCH_OFFSET:08X}: 0x{current:02X} → 0x{data[PATCH_OFFSET]:02X}")
    print(f"  {detail}")
    print(f"  Load and Save buttons are unaffected.")


if __name__ == "__main__":
    main()

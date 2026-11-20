# "Add to tadpod" iPhone shortcut

One tap: scan someone's LinkedIn QR code, queue them in tadpod, land on their LinkedIn
profile ready to Connect. It asks no questions; you add context later from your laptop.

## Build it (about 3 minutes)

Open the **Shortcuts** app, tap **+**, and name it **Add to tadpod**. Add these actions in order:

1. **Scan QR or Barcode.**
2. **Expand URL** with input *QR/Barcode*. This resolves short links to the full profile URL.
3. **Format Date**: date *Current Date*, format **ISO 8601**, **Include Time** on.
4. **Text**: insert *Formatted Date*, type a space, then insert *URLs* (from Expand URL).
5. **Append to Text File**: append *Text* to the file path `tadpod/inbox.txt`, with
   **Make New Line** on. This writes to `iCloud Drive/Shortcuts/tadpod/inbox.txt`.
6. **Open URLs** with input *URLs* (from Expand URL). LinkedIn opens on their profile.

## Make it one tap

- **Home Screen:** shortcut ⋯ → Share → Add to Home Screen.
- **Lock Screen:** customize the Lock Screen → add a Shortcuts widget.
- **Action button** (iPhone 15 Pro and later): Settings → Action Button → Shortcut → Add to tadpod.

## Check it works

Run it on any LinkedIn QR code (open LinkedIn on another phone → search bar → QR icon →
My code). On your Mac, `~/Library/Mobile Documents/iCloud~is~workflow~my~workflows/Documents/tadpod/inbox.txt`
should show a new line within a minute. Then ask your AI app "process my tadpod queue."

If your Shortcuts version saves the file elsewhere, set `TADPOD_INBOX` to that folder.

## Privacy

The inbox file holds only profile URLs and timestamps. It syncs through iCloud (end-to-end
encrypted if you've turned on Advanced Data Protection) and tadpod clears each line after
pulling it.

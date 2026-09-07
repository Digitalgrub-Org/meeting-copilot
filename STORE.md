# Shipping Cue on the Microsoft Store

What the Store requires, what Cue already satisfies, and what is still on you.
Requirements below are from Microsoft's own publishing docs; links at the bottom.

Cue goes in as an **unpackaged Win32 app**: the Store hosts the listing and points at
your own installer URL. No MSIX repackaging needed, so `installer/Cue.iss` is the
artifact you ship.

---

## Ready

| Requirement | Status |
|---|---|
| Installer is `.exe` or `.msi` | `CueSetup.exe`, Inno Setup |
| Installer is fully offline, downloads nothing during setup | Everything is bundled. Speech models download later, on first use, which is the app running and not the installer |
| App is uninstallable by normal means | Inno registers an uninstaller and a Programs and Features entry |
| Privacy policy exists | [PRIVACY.md](PRIVACY.md) — still needs hosting at a public URL |
| Discloses handling of personal information | Privacy policy plus a first-run notice in the app |
| No telemetry or hidden network calls | Only the AI providers you opt into, plus model downloads |
| Per-user install, no admin prompt | `PrivilegesRequired=lowest` |
| Builds from a clean checkout | Installer path is relative as of this release |

## Still on you

**0. The bundled FFmpeg is GPL, and that probably blocks the Store specifically.**
Checked every PyAV wheel from 12 to 18: all bundle `libx264` and `libx265`, and 12.3.0
and 13.1.0 record `--enable-gpl` outright. There is no LGPL-only wheel to pin, and
`faster_whisper` imports `av` at module level so it cannot be excluded. So the packaged
build contains GPL v3 code.

For a GitHub release that is workable — offer the installer under GPL, with source
already public. For the **Store** it is a real problem, because Microsoft's terms have
historically conflicted with GPL v3. If the Store is the goal, you need a custom
audio-only FFmpeg build, which means owning a media stack you rebuild on every security
update. Options and costs in [THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md).

Source distribution is unaffected by any of this.

**1. Code signing. This is the real gate.**
Microsoft requires the installer *and its PE files* to be signed with a certificate
chaining to a CA in the Microsoft Trusted Root Program. **Self-signed will be
rejected.**

The free route for an open-source project is **[SignPath Foundation](https://signpath.org/)**,
which signs OSS releases at OV level at no cost. Their
[conditions](https://signpath.org/terms.html): an OSI license with no proprietary
components (Cue: MIT, all bundled components open source), actively maintained, a
**public** repository, a **published release** in the form to be signed, and the
functionality described on the download page. They sign artifacts produced by **CI**,
not hand-uploaded files, which is why `.github/workflows/build.yml` exists. Apply at
signpath.org once the first release is up; approval takes days to weeks. After approval,
add their `signpath/github-action-submit-signing-request` step after the installer
build and attach the signed output.

The paid alternative, Microsoft's Azure Artifact Signing at $9.99/month, is limited to
US, Canada, EU and UK entities, so it is not available here.

Note that a PyInstaller bundle is not one file: `dist/Cue/_internal` holds hundreds
of `.dll` and `.pyd` files. Sign them before packaging, then sign the installer:

```bash
signtool sign /fd SHA256 /tr http://timestamp.digicert.com /td SHA256 /f cert.pfx /p PASSWORD dist\Cue\Cue.exe
```

Run the same over `dist\Cue\_internal\*.dll` and `*.pyd`, then over
`installer\output\CueSetup.exe`.

**2. Host the installer at a versioned URL.**
Partner Center wants a download URL, and the binary behind it must never change.
Every update needs a *new* versioned URL. GitHub Releases fits this exactly:
`.../releases/download/v1.0.0/CueSetup.exe`. You own uptime for that URL.

**3. Publish the privacy policy at a URL.**
A repository file is not enough; Partner Center wants a link. GitHub Pages on this
repo is the cheapest route.

**4. Partner Center account and publisher name.**
Register, then reserve "Digitalgrub" as the publisher display name.

**5. Age rating.**
Complete the IARC questionnaire honestly. Cue has no game content, but it does
transcribe conversations and can send them to a third party when you enable that,
so answer the data questions accordingly.

**6. Listing assets.**
Description, screenshots, and a support contact. `promo/cue_ui.png` is a clean
empty-state shot; a screenshot with a real brief in it would sell better, and needs
to be captured from a meeting with fake or consented content.

**7. Declare the Ollama dependency in the listing.**
Cue's AI features need Ollama installed separately, or a Claude API key. A reviewer
who installs Cue and sees no brief will consider that a defect. Say it plainly in
the description, and note that capture and transcription work without it.
`installer/PREINSTALL.txt` already covers this during install.

---

## Before submitting

Do these in order. Steps 1 and 2 are not paperwork; they are the difference between
a working app and a broken one.

1. **Build from a pinned environment, never from a working Anaconda install.**
   PyInstaller bundles whatever is installed. `ctranslate2` 4.5 and newer crash on
   model load, and a frozen build gives the user no way to repair it. Build from
   `D:\CueData\build-venv`, which pins `ctranslate2==4.4.0` and
   `onnxruntime==1.18.1`.

   ```bash
   D:\CueData\build-venv\Scripts\python.exe -m PyInstaller Cue.spec --noconfirm
   ```

2. **Verify the frozen speech worker.** A frozen build has no interpreter to call, so
   it re-runs itself. If this prints a transcript, the worker dispatch is intact:

   ```bash
   dist\Cue\Cue.exe --cue-worker transcribe some-audio.m4a --model tiny
   ```

3. **Run one real meeting** with the live loop. Nothing else substitutes for it.
4. **Test the installer on a machine that has never had Python**, and confirm both
   speech features work there.
5. Sign everything, upload, submit.

---

## Sources

- [Get started with Microsoft Store publishing](https://learn.microsoft.com/en-us/windows/apps/publish/get-started)
- [App package requirements for MSI/EXE apps](https://learn.microsoft.com/en-us/windows/apps/publish/publish-your-app/msi/app-package-requirements)
- [Microsoft Store Policies](https://learn.microsoft.com/en-us/windows/apps/publish/store-policies)
- [Age ratings for MSI/EXE apps](https://learn.microsoft.com/en-us/windows/apps/publish/publish-your-app/msi/age-ratings)
- [Choose a distribution path for your Windows app](https://learn.microsoft.com/en-us/windows/apps/package-and-deploy/choose-distribution-path)

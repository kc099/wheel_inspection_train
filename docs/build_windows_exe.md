# Making the App Double-Clickable on the Production Machine

Two ways to get a double-click Wheel Inspection app. **Pick Option A** when the
production machine has conda with the requirements env (ours does: env
`depth`). Option B (PyInstaller) is only for machines with no Python at all.

---

## Option A — launcher into the conda env (recommended, our setup)

The production system already has conda and a fully-provisioned env called
`depth`, so there is nothing to "build". The repo ships
**`WheelInspection.bat`** in the project root, which:

1. finds conda (checks `%USERPROFILE%\anaconda3`, `miniconda3`,
   `C:\ProgramData\anaconda3` — edit `CONDA_ROOT` at the top if it lives
   elsewhere),
2. activates the `depth` env (edit `ENV_NAME` if the env is renamed),
3. `cd`s into the app folder so `models/`, `settings.json` etc. resolve,
4. runs `python main.py`, keeping the console open on a crash so the error
   stays readable.

### A1. Create the double-click app (5 minutes)

1. Copy the whole project folder onto the machine, e.g. `C:\WheelInspection\`.
   Any folder the operator can write to works — **not** `C:\Program Files`.
2. Open `C:\WheelInspection\WheelInspection.bat` in Notepad and check the two
   lines at the top: `CONDA_ROOT` (where conda is installed) and
   `ENV_NAME=depth`. The launcher auto-detects the usual conda locations, so
   normally you change nothing.
3. Double-click `WheelInspection.bat`. First-run checklist in the console:
   - `N model(s) loaded`
   - `Listening on COMx @ 19200 baud`
   - `Camera connected — live frames from http://…`
   If the COM port or camera URL is wrong, edit `settings.json` (same folder)
   and relaunch.
4. Right-click `WheelInspection.bat` → **Send to → Desktop (create shortcut)**.
5. Optional polish: rename the shortcut to *Wheel Inspection*; right-click →
   Properties → *Change Icon…* to set a custom `.ico`; set *Run: Minimized*
   if you want the console to start minimized to the taskbar (it keeps
   logging either way).

The desktop shortcut is the operator's app. The console window that opens with
it is the engineer log — don't hide it.

**Upgrade:** replace the `app/` folder and `main.py`; never overwrite
`models/`, `settings.json`, `users.json`, `history.db`.

### A2. Auto-start when the system is turned on

Two pieces: Windows must log in by itself, then launch the app. Total setup
~5 minutes, one time.

**Step 1 — auto-login (so power-on needs no keyboard):**

1. Win+R → `netplwiz` → untick *"Users must enter a user name and password to
   use this computer"* → Apply → enter the operator account's password.
   - If the checkbox is missing (Windows 10/11 with Hello): Settings →
     Accounts → Sign-in options → turn OFF *"For improved security, only allow
     Windows Hello sign-in"*, then repeat.
2. Skip this step if the machine is set to log in automatically already, or if
   the site requires a manual login for security — the app then starts right
   after someone logs in.

**Step 2 — launch the app at login:**

1. Win+R → `shell:startup` → Enter. An Explorer window opens
   (`…\AppData\…\Startup`).
2. Copy the *Wheel Inspection* desktop shortcut into that folder.
3. Done. Restart the machine to verify: power on → Windows logs in → the app
   opens, connects the camera, and starts listening on the COM port with no
   human touch. (The app's own retry loops handle the camera/network not
   being ready yet in the first seconds after boot.)

**Alternative for a delayed / more controlled start (optional):** use Task
Scheduler instead of the Startup folder when the camera or PLC needs extra
time after power-on:

1. Start → *Task Scheduler* → *Create Basic Task…* → name `Wheel Inspection`.
2. Trigger: *When I log on*. Action: *Start a program* →
   Program: `C:\WheelInspection\WheelInspection.bat`,
   *Start in*: `C:\WheelInspection`.
3. Finish, then open the task's Properties → Triggers → Edit → *Delay task
   for: 30 seconds*.
4. Remove the shortcut from `shell:startup` so it doesn't start twice.

---

## Option B — PyInstaller bundle (only for machines without Python)

> **Read first:** the code contains two PyInstaller-specific fixes that this
> option depends on — the frozen-aware data root (`app/core/paths.py`) and
> offline model loading (`pre_trained=False` in `app/inference/engine.py`).
> Don't remove them.

## 1. One-time setup (build machine)

Build in the same conda env the app runs in (`depth` on production, `dl` on
the dev box), so the bundled libraries are exactly the tested ones:

```powershell
conda activate depth
pip install pyinstaller
```

## 2. Build command

From the project root (`f:\wheel_inspection_py`):

```powershell
pyinstaller main.py `
  --name WheelInspection `
  --onedir `
  --noconfirm `
  --collect-all anomalib `
  --collect-all timm `
  --collect-all kornia `
  --collect-all torch `
  --collect-all torchvision `
  --collect-all cv2 `
  --collect-submodules serial
```

Notes on the choices:

- **`--onedir`, not `--onefile`.** With torch on board the bundle is several
  GB. `--onefile` would unpack all of it to a temp dir on *every* launch
  (minutes of startup, and disk churn). `--onedir` starts fast; "double-click"
  still works — see §4.
- **No `--noconsole`.** The console window IS the engineer log (camera
  connects, PLC triggers, per-model scores). Keep it. If a customer ever
  demands no console, add `--noconsole` — but then the log trail is gone
  until we add file logging.
- **`--collect-all` for anomalib/timm/kornia**: these import submodules
  dynamically at runtime; PyInstaller's static analysis misses them.
- `--collect-submodules serial` covers pyserial's platform backends.

The build lands in `dist\WheelInspection\` — the exe plus an `_internal\`
folder with all libraries. Expect 3–6 GB and a 10–20 minute first build.

## 3. Assemble the deployment folder

The app reads/writes its data **next to the exe** (that's what
`app/core/paths.py` does in a frozen build). Copy these into
`dist\WheelInspection\` alongside the exe:

```
dist\WheelInspection\
├── WheelInspection.exe        (built)
├── _internal\                 (built — never touch)
├── Assets\                    ← copy from the repo (logo)
├── models\                    ← copy the trained models to ship with
├── settings.json              ← copy, then EDIT for the site (COM port,
│                                 camera URL, baud rate)
└── (created on first run: users.json, history.db)
```

```powershell
Copy-Item Assets  dist\WheelInspection\Assets  -Recurse
Copy-Item models  dist\WheelInspection\models  -Recurse
Copy-Item settings.json dist\WheelInspection\
```

Zip `dist\WheelInspection\` and that zip is the release.

## 4. On the production machine

1. Unzip anywhere the operator has write access — e.g. `C:\WheelInspection\`.
   **Not** `C:\Program Files` (the app writes settings/history next to the
   exe, and Program Files is read-only for normal users).
2. Right-click `WheelInspection.exe` → *Send to → Desktop (create shortcut)*.
   The operator double-clicks that shortcut.
3. First launch: check the console says `Listening on COMx` and
   `Camera connected`. Edit `settings.json` (same folder as the exe) if the
   COM port or camera URL differs, then restart the app.

## 5. Training on the production machine — internet caveat

**Recognition works fully offline.** Training a NEW model does not: building
the backbone downloads ImageNet weights from the HuggingFace Hub on first use.
Two options for an offline site:

- Pre-seed the cache: on the build machine, run one training, then copy
  `%USERPROFILE%\.cache\huggingface` (and `%USERPROFILE%\.cache\torch` if
  present) to the same location on the production machine.
- Or accept that training needs a temporary internet connection; day-to-day
  inspection never does.

## 6. Upgrading a deployed site

Replace **only** `WheelInspection.exe` and `_internal\`. Never overwrite
`models\`, `settings.json`, `users.json`, or `history.db` — that's the site's
data, and it lives outside `_internal\` precisely so upgrades can't destroy it.

## 7. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `ModuleNotFoundError: xyz` at launch | PyInstaller missed a dynamic import. Add `--collect-all xyz` (or `--hidden-import xyz`) and rebuild. |
| Exe flagged/deleted by antivirus | Common for PyInstaller. Whitelist the folder, or sign the exe. |
| Blank window / Qt plugin error | Conflicting Qt installs in the build env. Build in a clean env with only PySide6 (no PyQt5). |
| Training fails with an HF Hub / connection error | See §5 — offline machine, backbone weights not cached. |
| Settings changes ignored | Editing the wrong `settings.json` — the live one sits next to the exe, not in `_internal\`. |
| App works from source but paths are weird frozen | Something re-derived paths from `__file__` instead of `app.core.paths.ROOT`. All data paths must go through `paths.py`. |

## 8. Smoke-test checklist (run after every build)

1. Double-click the exe on a machine **without Python** — window opens.
2. Console shows models loaded + `Listening on COMx`.
3. Camera connects; Live Feed shows video; unplug camera → panel shows
   "Camera disconnected — reconnecting…" and recovers on replug.
4. Upload an image → Run Inspection → verdict appears, scores print in the
   console.
5. PLC trigger → frame captured → result + 13-byte frame sent (console log).
6. Train a small test model end-to-end, verify it classifies, then delete it.
7. Close and relaunch — settings, users, and history persisted.

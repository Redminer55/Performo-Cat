import ctypes
from ctypes import wintypes
import json
import math
import os
import random
import subprocess
import sys
import time
import zipfile, shutil
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox
from PIL import Image, ImageChops, ImageColor, ImageDraw, ImageFont, ImageOps, ImageTk
import win32com.client

if getattr(sys, "frozen", False):
    # Als PyInstaller-EXE gestartet
    FOLDER = str(Path(sys.executable).parent)
else:
    # Als Python-Datei gestartet
    FOLDER = str(Path(__file__).resolve().parent)

def create_desktop_shortcut():
    """Create or replace the Bongo Cat shortcut on the current user's desktop."""
    resource_folder = getattr(sys, "_MEIPASS", FOLDER)
    # The project ships icon.ico (there is no Title.ico). In a PyInstaller
    # build, include it as a bundled data file so _MEIPASS can resolve it.
    icon_path = os.path.join(resource_folder, "icon.ico")

    if getattr(sys, "frozen", False):
        target_path = sys.executable
        arguments = ""
        working_directory = os.path.dirname(sys.executable)
    else:
        target_path = sys.executable
        if target_path.lower().endswith("python.exe"):
            pythonw_path = target_path[:-10] + "pythonw.exe"
            if os.path.isfile(pythonw_path):
                target_path = pythonw_path
        arguments = '"' + os.path.abspath(__file__) + '"'
        working_directory = FOLDER

    # Escape values as PowerShell single-quoted string literals.
    ps_values = [target_path, arguments, working_directory, icon_path]
    ps_values = ["'" + value.replace("'", "''") + "'" for value in ps_values]
    script = (
        "$target, $argsText, $workDir, $icon = " + ", ".join(ps_values) + "; "
        "$shell = New-Object -ComObject WScript.Shell; "
        "$desktop = $shell.SpecialFolders('Desktop'); "
        "$linkPath = Join-Path $desktop 'Bongo Cat.lnk'; "
        "$desktopFolders = @($desktop); "
        "$publicDesktop = [Environment]::GetFolderPath('CommonDesktopDirectory'); "
        "if ($publicDesktop -and $publicDesktop -ne $desktop) { $desktopFolders += $publicDesktop }; "
        "foreach ($folder in $desktopFolders) { "
        "if (Test-Path -LiteralPath $folder) { "
        "Get-ChildItem -LiteralPath $folder -File -ErrorAction SilentlyContinue | "
        "Where-Object { $_.BaseName -ieq 'Bongo Cat' -and $_.Extension -in '.lnk', '.url' } | "
        "Remove-Item -Force -ErrorAction SilentlyContinue } }; "
        "$link = $shell.CreateShortcut($linkPath); "
        "$link.TargetPath = $target; $link.Arguments = $argsText; "
        "$link.WorkingDirectory = $workDir; "
        "if (Test-Path -LiteralPath $icon) { $link.IconLocation = ($icon + ',0') }; "
        "$link.Save()"
    )
    subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden",
         "-Command", script],
        check=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )

# Set True to make every installed skin and hat available without chests.
UNLOCK_ALL_ITEMS = os.path.exists(os.path.join(FOLDER, "cheats.cnfg"))
DEX_PAGE_SIZE = 36
HAT_ART_SCALE = 1 / 5
HAT_REFERENCE_SCALE = 0.35

class WinPoint(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


class WinSize(ctypes.Structure):
    _fields_ = [("cx", wintypes.LONG), ("cy", wintypes.LONG)]


class BlendFunction(ctypes.Structure):
    _fields_ = [("BlendOp", wintypes.BYTE), ("BlendFlags", wintypes.BYTE),
                ("SourceConstantAlpha", wintypes.BYTE), ("AlphaFormat", wintypes.BYTE)]


class BitmapInfoHeader(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
                ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD)]


class RGBQuad(ctypes.Structure):
    _fields_ = [("rgbBlue", wintypes.BYTE), ("rgbGreen", wintypes.BYTE),
                ("rgbRed", wintypes.BYTE), ("rgbReserved", wintypes.BYTE)]


class BitmapInfo(ctypes.Structure):
    _fields_ = [("bmiHeader", BitmapInfoHeader), ("bmiColors", RGBQuad * 1)]


class BongoCatPro:
    """Small, always-on-top Bongo Cat overlay with system-wide key tracking."""

    HEART_TOP_PADDING = 96
    HEART_RISE_DISTANCE = 55

    # VK codes are used by the low-level Windows keyboard hook.
    LEFT_KEYS = {
        0x41, 0x51, 0x57, 0x45, 0x52, 0x54, 0x59, 0x46, 0x47, 0x5A, 0x44, 0x53,
        0x58, 0x43, 0x56, 0x42, 0x31, 0x32, 0x33, 0x34, 0x35,
        0x25, 0xA0, 0xA2, 0x09, 0x100,
    }
    RIGHT_KEYS = {
        0x55, 0x49, 0x4F, 0x50, 0x48, 0x4A, 0x4B, 0x4C, 0x4D,
        0x4E, 0x36, 0x37, 0x38, 0x39, 0x30, 0x26, 0x27, 0xA1,
        0xA3, 0x20, 0x0D, 0x08, 0x101,
    }

    def __init__(self, root):
        self.root = root
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        # Keep the chroma key close to the sprites' dark contour so edge blending stays subtle.
        self.transparent_color = "#302929"
        self.root.config(bg=self.transparent_color)
        self.root.wm_attributes("-transparentcolor", self.transparent_color)

        self.current_skin = "Default"
        self.bar_accent_color = "#d8e0e9"
        self.current_hat = None
        self.unlocked_skins = {"Default"}
        self.unlocked_hats = set()
        self.dex_visible_limit = DEX_PAGE_SIZE
        self.hat_visible_limit = DEX_PAGE_SIZE
        self.inventory_thumbnail_cache = {}
        self.menu_scroll_positions = {}
        self.hat_image = None
        self.display_hat_image = None
        self.cat_flipped = False
        self.cat_rotation = 0
        self.sprite_transform_cache = {}
        self.last_overlay_counter = None
        self.last_overlay_chest_state = None
        self.refresh_job = None
        self._counter_font = ImageFont.truetype(r"C:\Windows\Fonts\segoeuib.ttf", 12)
        self._counter_text = None
        self._counter_text_width = 0
        self.available_skin_cache = None
        self.available_hat_cache = None

        self.folder = os.path.join(FOLDER, "Bongo_sprites", self.current_skin)
        self.counter_file = os.path.join(FOLDER, "bongo_taps.json")
        self.window_x = 100
        self.window_y = 100
        self.scale_factor = 0.35
        self.chest_interval = 30 * 60
        self.chest_progress = 0.0
        self.pending_chests = 0
        self.chest_last_tick = time.monotonic()
        self.chest_last_save = self.chest_last_tick
        self.unlock_message = ""
        self.unlock_message_until = 0.0
        self.chest_animation_started = 0.0
        self.chest_animation_until = 0.0
        self.chest_bubble_until = 0.0
        self.chest_bubble_text = ""
        self.chest_reward_started = 0.0
        self.chest_reward_until = 0.0
        self.chest_reward_kind = None
        self.chest_reward_name = ""
        self.chest_reward_preview = None
        self.pressed_keys = set()
        self.pending_cat_transforms = []
        self.tap_visual_keys = set()
        self.tap_visual_until = 0.0
        self.next_tap_paw = "left"
        self.tap_paw = "left"
        self.total_presses = self.load_counter()
        if UNLOCK_ALL_ITEMS:
            self.unlocked_skins.update(self.available_skins())
            self.unlocked_hats.update(self.available_hats())
        self.save_job = None
        self.counter_needs_save = False
        self.love_animation_until = 0.0
        self.floating_hearts = []
        self.heart_serial = 0
        self.heart_base_image = self.make_heart_image()
        self.is_ghost = False
        self.hook = None
        self.hook_callback = None

        self.load_and_resize_images()
        self.canvas = tk.Canvas(root, width=self.width, height=self.height,
                                bg=self.transparent_color, highlightthickness=0)
        self.canvas.pack()
        self.create_sprite_overlay()
        self.canvas.bind("<Button-1>", self.start_drag)
        self.canvas.bind("<ButtonRelease-1>", self.finish_drag)
        self.canvas.bind("<B1-Motion>", self.do_drag)
        self.canvas.bind("<Button-3>", self.toggle_ghost_mode)

        self.start_keyboard_hook()
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.update_display()

    def set_cheats(self):
        global UNLOCK_ALL_ITEMS
        if messagebox.askokcancel("Bongo Cat", "Warnung: Du bist dabei alles Freizuschalten, diese Aktion kann nicht rückgängig gemacht werden!\nBist du sicher, dass du fortfahren willst?"):
            UNLOCK_ALL_ITEMS = True
            with open(os.path.join(FOLDER, "cheats.cnfg"), "w") as f:
                f.write("True")

            messagebox.showinfo("Bongo Cat", "Alles wurde erfolgreich Freigeschaltet, bitte starte das Spiel erneut um die Änderungen anzuwenden.\n(Das Programm beendet sich Automatisch wenn du auf Okay drückst)")
            sys.exit(1)
        else:
            self.cheats_var = False

    def get_startup_shortcut_path(self):
        startup_folder = (
            Path(os.environ["APPDATA"])
            / "Microsoft"
            / "Windows"
            / "Start Menu"
            / "Programs"
            / "Startup"
        )
        return startup_folder / "Bongo Cat.lnk"


    def set_autostart(self,enabled):
        shortcut_path = self.get_startup_shortcut_path()

        if enabled:
            shell = win32com.client.Dispatch("WScript.Shell")
            shortcut = shell.CreateShortCut(str(shortcut_path))

            if getattr(sys, "frozen", False):
                # Wenn die App als EXE läuft
                shortcut.TargetPath = sys.executable
                shortcut.WorkingDirectory = str(
                    Path(sys.executable).parent
                )
            else:
                # Wenn die App direkt als Python-Skript läuft
                shortcut.TargetPath = sys.executable
                shortcut.Arguments = f'"{Path(__file__).resolve()}"'
                shortcut.WorkingDirectory = str(
                    Path(__file__).resolve().parent
                )

            shortcut.save()

        else:
            # Nur die eigene, fest benannte Verknüpfung löschen
            if shortcut_path.exists():
                shortcut_path.unlink()

    def load_and_resize_images(self):
        try:
            self.load_bar_accent_color()
            images = [Image.open(os.path.join(self.folder, name))
                      for name in ("idle.png", "left.png", "right.png", "both.png")]
            source_w, source_h = images[0].size
            new_w = max(1, int(source_w * self.scale_factor))
            new_h = max(1, int(source_h * self.scale_factor))
            self.sprite_width, self.sprite_height = new_w, new_h
            self.extra_bottom_space = 0
            # Leave a small, consistent clearance above the footer bar.
            self.width, self.height = new_w, new_h + 58
            self.root.geometry(f"{self.width}x{self.height}+{self.window_x}+{self.window_y}")
            self.sprite_frames = [img.convert("RGBA").resize(
                (new_w, new_h), Image.Resampling.LANCZOS) for img in images]
            self.load_hat_image()
            self.has_images = True
        except Exception as error:
            print(f"Fehler beim Laden der Bilder: {error}")
            self.width = self.height = 200
            self.sprite_width = self.sprite_height = 0
            self.extra_bottom_space = 0
            self.root.geometry(f"200x200+{self.window_x}+{self.window_y}")
            self.sprite_frames = []
            self.has_images = False

    def load_bar_accent_color(self):
        self.bar_accent_color = "#d8e0e9"
        config_path = os.path.join(self.folder, "bar.cnfg")
        try:
            with open(config_path, "r", encoding="utf-8-sig") as config_file:
                color = config_file.read().strip()
            if color:
                if len(color) in (3, 6) and all(char in "0123456789abcdefABCDEF" for char in color):
                    color = "#" + color
                if color.startswith("#") and len(color) == 4:
                    color = "#" + "".join(char * 2 for char in color[1:])
                ImageColor.getrgb(color)
                self.bar_accent_color = color
        except (OSError, ValueError):
            pass

    def load_hat_image(self):
        self.hat_image = None
        self.display_hat_image = None
        if not self.current_hat or not self.sprite_width or not self.sprite_height:
            return
        try:
            hat_path = os.path.join(FOLDER, "Bongo_hats", f"{self.current_hat}.png")
            with Image.open(hat_path) as image:
                hat = image.convert("RGBA")
                # Scale visible hat art to one third; transparent canvas
                # padding must not dilute the effective size.
                bounds = hat.getchannel("A").getbbox()
                if bounds:
                    hat = hat.crop(bounds)
                scale = HAT_ART_SCALE * (self.scale_factor / HAT_REFERENCE_SCALE)
                new_size = (max(1, round(hat.width * scale)),
                            max(1, round(hat.height * scale)))
                self.hat_image = hat.resize(new_size, Image.Resampling.LANCZOS)
            self.display_hat_image = (ImageOps.mirror(self.hat_image)
                                      if self.cat_flipped else self.hat_image)
        except OSError:
            self.current_hat = None

    def create_sprite_overlay(self):
        self.layered_api = ctypes.WinDLL("user32", use_last_error=True)
        self.gdi_api = ctypes.WinDLL("gdi32", use_last_error=True)
        self.sprite_overlay = tk.Toplevel(self.root)
        self.sprite_overlay.overrideredirect(True)
        self.sprite_overlay.attributes("-topmost", True)
        self.sprite_overlay.geometry(f"{self.width}x{self.height}+100+100")
        self.sprite_overlay.update_idletasks()
        self.sprite_overlay.bind("<Button-1>", self.on_sprite_click)
        self.sprite_overlay.bind("<ButtonRelease-1>", self.finish_drag)
        self.sprite_overlay.bind("<Button-3>", self.toggle_ghost_mode)
        self.sprite_overlay.bind("<B1-Motion>", self.do_drag)

        hwnd = self.sprite_overlay.winfo_id()
        self.layered_api.GetAncestor.argtypes = [ctypes.c_void_p, wintypes.UINT]
        self.layered_api.GetAncestor.restype = ctypes.c_void_p
        hwnd = self.layered_api.GetAncestor(hwnd, 2) or hwnd
        self.overlay_hwnd = hwnd
        root_hwnd = self.layered_api.GetAncestor(self.root.winfo_id(), 2)
        self.root_hwnd = root_hwnd or self.root.winfo_id()
        get_style = getattr(self.layered_api, "GetWindowLongPtrW", self.layered_api.GetWindowLongW)
        set_style = getattr(self.layered_api, "SetWindowLongPtrW", self.layered_api.SetWindowLongW)
        get_style.argtypes = [ctypes.c_void_p, ctypes.c_int]
        get_style.restype = ctypes.c_ssize_t
        set_style.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_ssize_t]
        set_style.restype = ctypes.c_ssize_t
        ex_style = get_style(hwnd, -20)
        ex_style |= 0x00080000 | 0x08000000 | 0x00000080
        set_style(hwnd, -20, ex_style)
        self.overlay_get_style = get_style
        self.overlay_set_style = set_style
        self.last_overlay_sprite = None
        self.last_overlay_position = None
        self.last_overlay_size = None
        self.last_overlay_ghost = None
        self.last_overlay_had_hearts = False
        self.last_overlay_chest_state = None
        self.overlay_horizontal_padding = 0
        self.overlay_render_width = self.width
        self.overlay_error_shown = False

        self.layered_api.GetDC.argtypes = [ctypes.c_void_p]
        self.layered_api.GetDC.restype = ctypes.c_void_p
        self.layered_api.ReleaseDC.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        self.layered_api.ReleaseDC.restype = ctypes.c_int
        self.layered_api.UpdateLayeredWindow.argtypes = [
            ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(WinPoint),
            ctypes.POINTER(WinSize), ctypes.c_void_p, ctypes.POINTER(WinPoint),
            wintypes.DWORD, ctypes.POINTER(BlendFunction), wintypes.DWORD,
        ]
        self.layered_api.UpdateLayeredWindow.restype = wintypes.BOOL
        self.layered_api.SetWindowPos.argtypes = [
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
            ctypes.c_int, ctypes.c_int, wintypes.UINT,
        ]
        self.layered_api.SetWindowPos.restype = wintypes.BOOL

        self.gdi_api.CreateCompatibleDC.argtypes = [ctypes.c_void_p]
        self.gdi_api.CreateCompatibleDC.restype = ctypes.c_void_p
        self.gdi_api.DeleteDC.argtypes = [ctypes.c_void_p]
        self.gdi_api.DeleteDC.restype = wintypes.BOOL
        self.gdi_api.CreateDIBSection.argtypes = [
            ctypes.c_void_p, ctypes.POINTER(BitmapInfo), wintypes.UINT,
            ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p, wintypes.DWORD,
        ]
        self.gdi_api.CreateDIBSection.restype = ctypes.c_void_p
        self.gdi_api.SelectObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        self.gdi_api.SelectObject.restype = ctypes.c_void_p
        self.gdi_api.DeleteObject.argtypes = [ctypes.c_void_p]
        self.gdi_api.DeleteObject.restype = wintypes.BOOL

        self.sprite_overlay.deiconify()

    def render_sprite_overlay(self, sprite):
        width, height = self.width, self.height
        physical_size = (height, width) if self.cat_rotation % 2 else (width, height)
        reward_card_width = physical_size[0]
        if self.chest_reward_until > time.monotonic():
            reward_name = self.chest_reward_name.replace("_", " ")
            reward_font = ImageFont.truetype(r"C:\Windows\Fonts\segoeuib.ttf", 12)
            reward_probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
            reward_text_width = reward_probe.textbbox(
                (0, 0), reward_name, font=reward_font)[2]
            reward_card_width = min(320, max(250, reward_text_width + 124))
        overlay_width = max(physical_size[0], reward_card_width)
        self.overlay_horizontal_padding = (overlay_width - physical_size[0]) // 2
        self.overlay_render_width = overlay_width
        overlay_size = (overlay_width, physical_size[1] + self.HEART_TOP_PADDING)
        position = (self.root.winfo_rootx() - self.overlay_horizontal_padding,
                    self.root.winfo_rooty() - self.HEART_TOP_PADDING)
        if position != self.last_overlay_position:
            self.layered_api.SetWindowPos(
                self.overlay_hwnd, ctypes.c_void_p(-1), *position, *overlay_size,
                0x0040 | 0x0010 | 0x0200
            )
            self.last_overlay_position = position

        sprite_key = id(sprite) if sprite is not None else None
        has_hearts = bool(self.floating_hearts)
        chest_state = (self.pending_chests, self.total_presses >= 1000,
                       bool(self.locked_cosmetics()))
        if (not has_hearts and not self.last_overlay_had_hearts
                and sprite_key == self.last_overlay_sprite
                and overlay_size == self.last_overlay_size
                and self.is_ghost == self.last_overlay_ghost
                and self.total_presses == self.last_overlay_counter
                and chest_state == self.last_overlay_chest_state
                and self.love_animation_until <= time.monotonic()
                and self.chest_animation_until <= time.monotonic()
                and self.chest_bubble_until <= time.monotonic()
                and self.chest_reward_until <= time.monotonic()):
            return

        frame = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        # Draw the counter/menu first so the cat remains above it where they overlap.
        draw = ImageDraw.Draw(frame)
        bar_y, bar_bottom = height - 28, height - 4
        draw.rectangle((7, bar_y, width - 35, bar_bottom),
                       fill=self.bar_accent_color, outline="#000000")
        draw.text((13, bar_y + 4), f"{self.total_presses:,}".replace(",", " "),
                  fill="#000000", font=self._counter_font)
        menu_left, menu_right = width - 31, width - 7
        draw.rectangle((menu_left, bar_y, menu_right, bar_bottom),
                       fill=self.bar_accent_color, outline="#000000")
        menu_cx, menu_cy = width - 19, (bar_y + bar_bottom) // 2
        for line_y in (menu_cy - 5, menu_cy, menu_cy + 5):
            draw.line((menu_cx - 6, line_y, menu_cx + 6, line_y), fill="#000000", width=2)

        if sprite is not None:
            x = (width - sprite.width) // 2 + self.sprite_horizontal_offset()
            y = height - self.sprite_bottom_gap() - sprite.height
            frame.alpha_composite(sprite, (x, y))
            if self.display_hat_image is not None:
                hat_x = (x + (sprite.width - self.display_hat_image.width) // 2
                         - self.sprite_horizontal_offset())
                hat_y = (y + int(sprite.height * 0.12)
                         - self.display_hat_image.height // 2
                         + max(1, round(sprite.height * 0.28)))

                if self.current_skin == "Soldat":
                    hat_x -= round(sprite.width * 0.15)
                
                frame.alpha_composite(self.display_hat_image, (hat_x, hat_y))

        now = time.monotonic()
        self.floating_hearts[:] = [
            heart for heart in self.floating_hearts
            if now - heart[0] < self.heart_travel_time(heart[2])
        ]

        if self.cat_rotation:
            frame = frame.rotate(-90 * self.cat_rotation, expand=True)
        rotated_frame = frame
        frame = Image.new("RGBA", overlay_size, (0, 0, 0, 0))
        frame.alpha_composite(rotated_frame,
                              (self.overlay_horizontal_padding, self.HEART_TOP_PADDING))
        if physical_size != (self.root.winfo_width(), self.root.winfo_height()):
            self.root.geometry(f"{physical_size[0]}x{physical_size[1]}")
            self.canvas.config(width=physical_size[0], height=physical_size[1])

        # Hearts are positioned in the rotated frame, then rise vertically on screen.
        for started, heart_x, start_y in self.floating_hearts:
            age = now - started
            travel_time = self.heart_travel_time(start_y)
            progress = min(1.0, age / travel_time)
            eased = 1 - (1 - progress) ** 2
            if self.cat_rotation == 1:
                screen_x, screen_y = height - 1 - start_y, heart_x
            elif self.cat_rotation == 2:
                screen_x, screen_y = width - 1 - heart_x, height - 1 - start_y
            elif self.cat_rotation == 3:
                screen_x, screen_y = start_y, width - 1 - heart_x
            else:
                screen_x, screen_y = heart_x, start_y
            screen_x += self.overlay_horizontal_padding
            heart_y = (self.HEART_TOP_PADDING + screen_y
                       - int(self.HEART_RISE_DISTANCE * eased))
            fade_duration = min(1.5, travel_time)
            fade = min(1.0, max(0.0, (age - travel_time + fade_duration) / fade_duration))
            heart = self.heart_base_image.copy()
            alpha = heart.getchannel("A")
            alpha = alpha.point(lambda value: round(value * (1.0 - fade)))
            heart.putalpha(alpha)
            frame.alpha_composite(heart, (int(screen_x - heart.width / 2),
                                          int(heart_y - heart.height / 2)))

        # Chest reward animation stays upright on screen, even if the cat is rotated.
        if self.chest_animation_until > now:
            age = now - self.chest_animation_started
            progress = min(1.0, max(0.0, age / 1.7))
            eased = 1 - (1 - progress) ** 3
            chest_x = frame.width // 2
            chest_y = self.HEART_TOP_PADDING + 68 - int(10 * math.sin(progress * math.pi))
            chest_left, chest_right = chest_x - 23, chest_x + 23
            chest_top, chest_bottom = chest_y - 2, chest_y + 27
            draw = ImageDraw.Draw(frame)
            # Warm glow and the chest body.
            glow = max(0, 1 - progress)
            for radius in (33, 25):
                color = (255, 205, 91, round(35 * glow))
                draw.ellipse((chest_x - radius, chest_y - radius,
                              chest_x + radius, chest_y + radius), fill=color)
            draw.rounded_rectangle((chest_left, chest_y + 4, chest_right, chest_bottom),
                                   radius=4, fill="#a95e2c", outline="#59351f", width=2)
            draw.rectangle((chest_left + 2, chest_y + 13, chest_right - 2, chest_y + 16),
                           fill="#e4aa4b")
            draw.rectangle((chest_x - 4, chest_y + 10, chest_x + 4, chest_y + 20),
                           fill="#ffd66d", outline="#80542a")
            # The lid swings open as the reward appears.
            lid_lift = int(16 * eased)
            draw.polygon(((chest_left, chest_y + 4),
                          (chest_left + 3, chest_top - lid_lift),
                          (chest_right - 3, chest_top - lid_lift),
                          (chest_right, chest_y + 4)),
                         fill="#d58a3e", outline="#59351f")
            draw.line((chest_left + 5, chest_y + 1, chest_right - 5, chest_y + 1),
                      fill="#f2c15e", width=2)
            # A few gold sparkles appear after the lid opens.
            sparkle_alpha = round(255 * min(1.0, eased * 1.6) * (1 - progress * 0.45))
            sparkle_color = (255, 220, 105, sparkle_alpha)
            for sx, sy, size in ((chest_x - 34, chest_y - 20, 4),
                                 (chest_x + 32, chest_y - 27, 5),
                                 (chest_x + 25, chest_y + 3, 3)):
                draw.line((sx - size, sy, sx + size, sy), fill=sparkle_color, width=2)
                draw.line((sx, sy - size, sx, sy + size), fill=sparkle_color, width=2)

        # Reward card slides down from above the cat and previews the unlocked item.
        if self.chest_reward_until > now:
            age = now - self.chest_reward_started
            slide = min(1.0, max(0.0, age / 0.28))
            slide = 1 - (1 - slide) ** 3
            item_name = self.chest_reward_name.replace("_", " ")
            item_font = ImageFont.truetype(r"C:\Windows\Fonts\segoeuib.ttf", 12)
            name_probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
            name_width = name_probe.textbbox((0, 0), item_name, font=item_font)[2]
            card_width = min(320, max(250, name_width + 124))
            card_height = 82
            card_x = (frame.width - card_width) // 2
            card_y = -card_height + int((card_height + 8) * slide)
            card = Image.new("RGBA", (card_width, card_height), (0, 0, 0, 0))
            card_draw = ImageDraw.Draw(card)
            card_draw.rounded_rectangle((2, 3, card_width - 2, card_height - 2),
                                        radius=12, fill="#fff4dd",
                                        outline="#67432e", width=2)
            card_draw.rounded_rectangle((6, 6, card_width - 6, 23), radius=7,
                                        fill="#ffc56e", outline="#b87836", width=1)
            title = "NEUER HUT" if self.chest_reward_kind == "hat" else "NEUER SKIN"
            title_font = ImageFont.truetype(r"C:\Windows\Fonts\segoeuib.ttf", 10)
            card_draw.text((card_width // 2, 10), title, fill="#67432e",
                           font=title_font, anchor="ma")
            if self.chest_reward_preview is not None:
                preview = self.chest_reward_preview
                card.alpha_composite(preview, (12, 29))
            text_x = 78
            available_name_width = card_width - text_x - 14
            while name_width > available_name_width and item_font.size > 8:
                item_font = ImageFont.truetype(
                    r"C:\Windows\Fonts\segoeuib.ttf", item_font.size - 1)
                name_width = name_probe.textbbox((0, 0), item_name, font=item_font)[2]
            if name_width > available_name_width:
                shortened_name = item_name
                while shortened_name and name_probe.textbbox(
                        (0, 0), shortened_name + "…", font=item_font)[2] > available_name_width:
                    shortened_name = shortened_name[:-1]
                item_name = shortened_name + "…"
            card_draw.text((text_x, 37), item_name, fill="#67432e", font=item_font)
            card_draw.line((text_x, 57, card_width - 14, 57), fill="#e5c28e", width=1)
            card_draw.text((text_x, 62), "Freigeschaltet!", fill="#8b5736",
                           font=ImageFont.truetype(r"C:\Windows\Fonts\segoeui.ttf", 10))
            frame.alpha_composite(card, (card_x, card_y))

        # Keep speech bubbles upright, with their tails pointing down at the cat.
        chest_bubble = self.chest_bubble_layout(frame.width)
        bubble_text = (chest_bubble[0] if chest_bubble else
                       self.chest_bubble_text if self.chest_bubble_until > now else "")
        if bubble_text:
            physical_width = frame.width
            draw = ImageDraw.Draw(frame)
            bubble_font = ImageFont.truetype(r"C:\Windows\Fonts\segoeuib.ttf", 12)
            if chest_bubble and bubble_text == chest_bubble[0]:
                _, bubble_x, bubble_y, bubble_width, text_width = chest_bubble
                # Compact cream chest popup with a tiny illustrated chest and pointer.
                draw.rounded_rectangle((bubble_x + 2, bubble_y + 3,
                                        bubble_x + bubble_width + 2, bubble_y + 31),
                                       radius=9, fill="#9b7653")
                draw.rounded_rectangle((bubble_x, bubble_y, bubble_x + bubble_width,
                                        bubble_y + 29), radius=9, fill="#fff4dd",
                                       outline="#67432e", width=2)
                icon_x, icon_y = bubble_x + 8, bubble_y + 7
                draw.rectangle((icon_x, icon_y + 5, icon_x + 15, icon_y + 15),
                               fill="#b86d34", outline="#67432e", width=1)
                draw.rectangle((icon_x - 1, icon_y + 2, icon_x + 16, icon_y + 7),
                               fill="#e7b252", outline="#67432e", width=1)
                draw.rectangle((icon_x + 6, icon_y + 7, icon_x + 9, icon_y + 12),
                               fill="#ffe08a")
                draw.polygon(((bubble_x + bubble_width // 2 - 7, bubble_y + 27),
                              (bubble_x + bubble_width // 2, bubble_y + 37),
                              (bubble_x + bubble_width // 2 + 7, bubble_y + 27)),
                             fill="#fff4dd", outline="#67432e")
                draw.line((bubble_x + bubble_width // 2 - 5, bubble_y + 27,
                           bubble_x + bubble_width // 2 + 5, bubble_y + 27),
                          fill="#fff4dd", width=2)
                draw.text((bubble_x + 30, bubble_y + 7), bubble_text,
                          fill="#67432e", font=bubble_font)
            else:
                text_width = draw.textbbox((0, 0), bubble_text, font=bubble_font)[2]
                bubble_width = max(70, text_width + 20)
                bubble_x = max(8, min(physical_width - bubble_width - 8,
                                      physical_width // 2 + 14))
                bubble_y = self.HEART_TOP_PADDING + 8
                draw.ellipse((bubble_x, bubble_y, bubble_x + bubble_width, bubble_y + 32),
                             fill="#ffffff", outline="#000000")
                tail_x = bubble_x + min(bubble_width - 20, max(20, bubble_width // 2))
                draw.polygon(((tail_x - 11, bubble_y + 27),
                              (tail_x, bubble_y + 39),
                              (tail_x + 11, bubble_y + 27)),
                             fill="#ffffff", outline="#000000")
                draw.line((tail_x - 10, bubble_y + 27, tail_x + 10, bubble_y + 27),
                          fill="#ffffff", width=2)
                draw.text((bubble_x + (bubble_width - text_width) // 2, bubble_y + 9),
                          bubble_text, fill="#000000", font=bubble_font)
        # UpdateLayeredWindow requires premultiplied BGRA pixels for proper per-pixel alpha.
        red, green, blue, alpha = frame.split()
        red = ImageChops.multiply(red, alpha)
        green = ImageChops.multiply(green, alpha)
        blue = ImageChops.multiply(blue, alpha)
        bgra = Image.merge("RGBA", (blue, green, red, alpha)).tobytes()

        width, height = frame.size
        info = BitmapInfo()
        info.bmiHeader = BitmapInfoHeader(
            ctypes.sizeof(BitmapInfoHeader), width, -height, 1, 32, 0,
            width * height * 4, 0, 0, 0, 0
        )
        screen_dc = self.layered_api.GetDC(None)
        memory_dc = self.gdi_api.CreateCompatibleDC(screen_dc)
        bits = ctypes.c_void_p()
        bitmap = self.gdi_api.CreateDIBSection(
            screen_dc, ctypes.byref(info), 0, ctypes.byref(bits), None, 0
        )
        if not bitmap or not bits:
            self.gdi_api.DeleteDC(memory_dc)
            self.layered_api.ReleaseDC(None, screen_dc)
            return

        old_bitmap = self.gdi_api.SelectObject(memory_dc, bitmap)
        ctypes.memmove(bits, bgra, len(bgra))
        destination = WinPoint(self.root.winfo_rootx() - self.overlay_horizontal_padding,
                               self.root.winfo_rooty() - self.HEART_TOP_PADDING)
        source = WinPoint(0, 0)
        size = WinSize(frame.width, frame.height)
        blend = BlendFunction(0, 0, 90 if self.is_ghost else 255, 1)
        updated = self.layered_api.UpdateLayeredWindow(
            self.overlay_hwnd, screen_dc, ctypes.byref(destination), ctypes.byref(size),
            memory_dc, ctypes.byref(source), 0, ctypes.byref(blend), 2
        )
        self.layered_api.SetWindowPos(
            self.overlay_hwnd, ctypes.c_void_p(-1), destination.x, destination.y,
            frame.width, frame.height, 0x0040 | 0x0010 | 0x0200
        )
        self.gdi_api.SelectObject(memory_dc, old_bitmap)
        self.gdi_api.DeleteObject(bitmap)
        self.gdi_api.DeleteDC(memory_dc)
        self.layered_api.ReleaseDC(None, screen_dc)
        if not updated and not self.overlay_error_shown:
            print(f"Sprite-Ebene konnte nicht aktualisiert werden (Windows-Fehler {ctypes.get_last_error()}).")
            self.overlay_error_shown = True
        self.last_overlay_sprite = sprite_key
        self.last_overlay_size = overlay_size
        self.last_overlay_ghost = self.is_ghost
        self.last_overlay_had_hearts = has_hearts
        self.last_overlay_counter = self.total_presses
        self.last_overlay_chest_state = chest_state

    def start_keyboard_hook(self):
        if os.name != "nt":
            return
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.user32 = user32
        ll_keyboard = 13  # WH_KEYBOARD_LL
        keydown_messages = {0x0100, 0x0104}
        keyup_messages = {0x0101, 0x0105}

        class KeyboardData(ctypes.Structure):
            _fields_ = [("vkCode", wintypes.DWORD),
                        ("scanCode", wintypes.DWORD),
                        ("flags", wintypes.DWORD),
                        ("time", wintypes.DWORD),
                        ("dwExtraInfo", ctypes.c_size_t)]

        hook_handle_type = ctypes.c_void_p
        hook_proc_type = ctypes.WINFUNCTYPE(
            ctypes.c_ssize_t, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM
        )
        user32.SetWindowsHookExW.argtypes = [ctypes.c_int, hook_proc_type,
                                              ctypes.c_void_p, wintypes.DWORD]
        user32.SetWindowsHookExW.restype = hook_handle_type
        user32.CallNextHookEx.argtypes = [hook_handle_type, ctypes.c_int,
                                           wintypes.WPARAM, wintypes.LPARAM]
        user32.CallNextHookEx.restype = ctypes.c_ssize_t
        user32.UnhookWindowsHookEx.argtypes = [hook_handle_type]
        user32.UnhookWindowsHookEx.restype = wintypes.BOOL

        def keyboard_callback(code, message, data):
            if code >= 0 and message in keydown_messages | keyup_messages:
                vk = ctypes.cast(data, ctypes.POINTER(KeyboardData)).contents.vkCode
                if message in keydown_messages:
                    if vk not in self.pressed_keys:
                        if vk in (0x46, 0x52):
                            self.pending_cat_transforms.append(vk)
                        self.total_presses += 1
                        self.pressed_keys.add(vk)
                        self.counter_needs_save = True
                        self.tap_paw = ("both" if len(self.pressed_keys) >= 2
                                        else self.next_tap_paw)
                        self.next_tap_paw = "right" if self.next_tap_paw == "left" else "left"
                        self.tap_visual_keys = {vk}
                        self.tap_visual_until = time.monotonic() + 0.14
                else:
                    self.pressed_keys.discard(vk)
            return user32.CallNextHookEx(self.hook, code, message, data)

        self.hook_callback = hook_proc_type(keyboard_callback)
        # A low-level hook runs in this process, so a null module handle is valid.
        user32.SetWindowsHookExW.argtypes = [ctypes.c_int, hook_proc_type,
                                              ctypes.c_void_p, wintypes.DWORD]
        self.hook = user32.SetWindowsHookExW(ll_keyboard, self.hook_callback, None, 0)
        if not self.hook:
            error = ctypes.get_last_error()
            print(f"Globale Tastaturüberwachung fehlgeschlagen (Windows-Fehler {error}).")

        # Count left, right and middle clicks globally as taps too.
        mouse_proc_type = ctypes.WINFUNCTYPE(
            ctypes.c_ssize_t, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM
        )

        class MouseData(ctypes.Structure):
            _fields_ = [("pt", WinPoint), ("mouseData", wintypes.DWORD),
                        ("flags", wintypes.DWORD), ("time", wintypes.DWORD),
                        ("dwExtraInfo", ctypes.c_size_t)]

        def mouse_callback(code, message, data):
            if code >= 0:
                # Include the two extra buttons commonly found on gaming mice.
                mouse_keys = {0x0201: (0x100,), 0x0204: (0x101,),
                              0x0207: (0x100, 0x101)}
                mouse_releases = {0x0202: (0x100,), 0x0205: (0x101,),
                                  0x0208: (0x100, 0x101)}
                if message in (0x020B, 0x020C):  # XBUTTONDOWN / XBUTTONUP
                    button = (ctypes.cast(data, ctypes.POINTER(MouseData))
                              .contents.mouseData >> 16) & 0xFFFF
                    extra_key = 0x102 if button == 1 else 0x103
                    if message == 0x020B:
                        mouse_keys[message] = (extra_key,)
                    else:
                        mouse_releases[message] = (extra_key,)
                if message in mouse_keys:
                    self.total_presses += 1
                    self.pressed_keys.update(mouse_keys[message])
                    self.counter_needs_save = True
                    self.tap_paw = ("both" if len(self.pressed_keys) >= 2
                                    else self.next_tap_paw)
                    self.next_tap_paw = "right" if self.next_tap_paw == "left" else "left"
                    self.tap_visual_keys = set(mouse_keys[message])
                    self.tap_visual_until = time.monotonic() + 0.14
                elif message in mouse_releases:
                    self.pressed_keys.difference_update(mouse_releases[message])
            return user32.CallNextHookEx(self.mouse_hook, code, message, data)

        self.mouse_hook_callback = mouse_proc_type(mouse_callback)
        user32.SetWindowsHookExW.argtypes = [ctypes.c_int, mouse_proc_type,
                                              ctypes.c_void_p, wintypes.DWORD]
        self.mouse_hook = user32.SetWindowsHookExW(14, self.mouse_hook_callback, None, 0)
        if not self.mouse_hook:
            error = ctypes.get_last_error()
            print(f"Globale Mausklick-Erfassung fehlgeschlagen (Windows-Fehler {error}).")

    def get_paw_state(self):
        if time.monotonic() >= self.tap_visual_until:
            return "idle"
        return self.tap_paw

    def transformed_sprite(self, sprite):
        key = (id(sprite), self.cat_flipped)
        if key not in self.sprite_transform_cache:
            self.sprite_transform_cache[key] = ImageOps.mirror(sprite) if self.cat_flipped else sprite
        return self.sprite_transform_cache[key]

    def cursor_is_over_cat(self):
        if not self.has_images:
            return False
        point = WinPoint()
        if not self.user32.GetCursorPos(ctypes.byref(point)):
            return False
        sprite = self.sprite_frames[{"idle": 0, "left": 1, "right": 2, "both": 3}[self.get_paw_state()]]
        sprite = self.transformed_sprite(sprite)
        x, y = self.screen_to_logical(point.x - self.root.winfo_rootx(),
                                      point.y - self.root.winfo_rooty())
        left = (self.width - sprite.width) // 2 + self.sprite_horizontal_offset()
        top = self.height - self.sprite_bottom_gap() - sprite.height
        x, y = x - left, y - top
        return (0 <= x < sprite.width and 0 <= y < sprite.height
                and sprite.getpixel((x, y))[3] > 32)

    def transform_cat_if_hovered(self, vk):
        if not self.cursor_is_over_cat():
            return
        if vk == 0x46:
            self.cat_flipped = not self.cat_flipped
            self.sprite_transform_cache.clear()
            self.display_hat_image = (ImageOps.mirror(self.hat_image)
                                      if self.hat_image and self.cat_flipped else self.hat_image)
            self.save_counter()
        else:
            pointer = WinPoint()
            self.user32.GetCursorPos(ctypes.byref(pointer))
            canonical_x, canonical_y = self.screen_to_logical(
                pointer.x - self.root.winfo_rootx(),
                pointer.y - self.root.winfo_rooty(),
            )
            self.cat_rotation = (self.cat_rotation + 1) % 4
            if self.cat_rotation == 1:
                rotated_x, rotated_y = self.height - 1 - canonical_y, canonical_x
            elif self.cat_rotation == 2:
                rotated_x, rotated_y = self.width - 1 - canonical_x, self.height - 1 - canonical_y
            elif self.cat_rotation == 3:
                rotated_x, rotated_y = canonical_y, self.width - 1 - canonical_x
            else:
                rotated_x, rotated_y = canonical_x, canonical_y
            new_width, new_height = ((self.height, self.width)
                                     if self.cat_rotation % 2 else (self.width, self.height))
            window_x = pointer.x - rotated_x
            window_y = pointer.y - rotated_y
            self.window_x, self.window_y = window_x, window_y
            self.root.geometry(f"{new_width}x{new_height}+{window_x}+{window_y}")
            self.last_overlay_position = None
            self.save_counter()
        self.update_display()
        self.position_settings_window()

    def screen_to_logical(self, x, y):
        if self.cat_rotation == 1:
            return y, self.height - 1 - x
        if self.cat_rotation == 2:
            return self.width - 1 - x, self.height - 1 - y
        if self.cat_rotation == 3:
            return self.width - 1 - y, x
        return x, y

    def sprite_vertical_offset(self):
        return 0

    def sprite_bottom_gap(self):
        # Smaller sprites sit slightly higher; larger sprites tuck closer to the bar.
        return max(0, round((0.55 - self.scale_factor) * 40))

    def sprite_horizontal_offset(self):
        return max(1, round(self.sprite_width * 0.02))

    def update_display(self):
        counter_text = f"{self.total_presses:,}".replace(",", " ")
        if counter_text != self._counter_text:
            probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
            self._counter_text_width = probe.textbbox(
                (0, 0), counter_text, font=self._counter_font)[2]
            self._counter_text = counter_text
        required_width = self._counter_text_width + 58
        if required_width > self.width:
            self.width = required_width
            self.canvas.config(width=self.width)
            self.root.geometry(f"{self.width}x{self.height}")

        self.canvas.delete("all")
        image = None
        if self.has_images:
            state = self.get_paw_state()
            image = self.transformed_sprite(
                self.sprite_frames[{"idle": 0, "left": 1, "right": 2, "both": 3}[state]])
        else:
            image = None
        self.render_sprite_overlay(image)
        if self.refresh_job is None:
            self.refresh_job = self.root.after(40, self.refresh_if_needed)

    def on_sprite_click(self, event):
        physical_width = self.overlay_render_width
        chest_bubble = self.chest_bubble_layout(physical_width)
        if chest_bubble:
            _, bubble_x, bubble_y, bubble_width, _ = chest_bubble
            if (bubble_x <= event.x <= bubble_x + bubble_width
                    and bubble_y <= event.y <= bubble_y + 38):
                self.open_cosmetic_chest("Skins")
                return "break"
        if event.y < self.HEART_TOP_PADDING:
            return "break"
        event_y = event.y - self.HEART_TOP_PADDING
        x, y = self.screen_to_logical(event.x - self.overlay_horizontal_padding, event_y)
        if (self.width - 31 <= x <= self.width - 7
                and self.height - 28 <= y <= self.height - 4):
            self.open_settings_menu(event)
            return "break"
        # Ignore transparent margins (including the heart animation area) so
        # the large overlay rectangle does not behave like a giant hitbox.
        sprite = self.sprite_frames[0] if self.has_images else None
        if sprite is not None:
            sprite_x = (self.width - sprite.width) // 2 + self.sprite_horizontal_offset()
            sprite_y = self.height - self.sprite_bottom_gap() - sprite.height
            if (sprite_x <= x <= sprite_x + sprite.width
                    and sprite_y <= y <= sprite_y + sprite.height):
                self.start_drag(event)
        return "break"

    def draw_love_animation(self):
        return

    @staticmethod
    def heart_travel_time(start_y):
        return 1.1

    def make_heart_image(self):
        emoji = "\U0001F496"
        font_path = r"C:\Windows\Fonts\seguiemj.ttf"
        try:
            font = ImageFont.truetype(font_path, 48)
        except OSError:
            try:
                font = ImageFont.truetype(r"C:\Windows\Fonts\seguisym.ttf", 48)
            except OSError:
                font = ImageFont.load_default()
        image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        try:
            draw.text((4, 2), emoji, font=font, embedded_color=True)
        except TypeError:
            draw.text((4, 2), emoji, font=font, fill="#ff5c86")
        bounds = image.getbbox()
        if bounds:
            image = image.crop(bounds)
        width, height = image.size
        scale = min(30 / width, 30 / height)
        resized = image.resize((max(1, int(width * scale)), max(1, int(height * scale))),
                               Image.Resampling.LANCZOS)
        square = Image.new("RGBA", (32, 32), (0, 0, 0, 0))
        square.alpha_composite(resized, ((32 - resized.width) // 2, (32 - resized.height) // 2))
        return square

    @staticmethod
    def blend_color(start, end, amount):
        start_rgb = tuple(int(start[index:index + 2], 16) for index in (1, 3, 5))
        end_rgb = tuple(int(end[index:index + 2], 16) for index in (1, 3, 5))
        mixed = tuple(round(a + (b - a) * amount) for a, b in zip(start_rgb, end_rgb))
        return "#" + "".join(f"{channel:02x}" for channel in mixed)

    def refresh_if_needed(self):
        self.refresh_job = None
        self.update_chest_progress()
        self.refresh_menu_stats()
        pending_transforms, self.pending_cat_transforms = self.pending_cat_transforms, []
        for vk in pending_transforms:
            self.transform_cat_if_hovered(vk)
        if self.counter_needs_save:
            self.counter_needs_save = False
            self.schedule_save()
        self.update_display()

    def refresh_menu_stats(self):
        taps_text = f"{self.total_presses:,} Taps".replace(",", " ")
        for attr_name, text in (("menu_taps_label", taps_text),
                                ("dex_stats_label",
                                 f"Freigeschaltet: {len(self.unlocked_skins) + len(self.unlocked_hats)}"
                                 f"/{len(self.available_skins()) + len(self.available_hats())}  •  "
                                 f"{self.total_presses:,} Taps".replace(",", " "))):
            label = getattr(self, attr_name, None)
            if label is None:
                continue
            try:
                if label.winfo_exists() and getattr(label, "_last_text", None) != text:
                    label.configure(text=text)
                    label._last_text = text
            except tk.TclError:
                pass

    def start_drag(self, event):
        self.drag_start_pointer = (event.x_root, event.y_root)
        self.drag_window_position = (self.window_x, self.window_y)
        event_y = event.y - (self.HEART_TOP_PADDING
                             if event.widget is self.sprite_overlay else 0)
        event_x, event_y = self.screen_to_logical(event.x, event_y)
        on_cat = False
        if self.has_images and event_y < self.height - 28:
            state = self.get_paw_state()
            sprite = self.transformed_sprite(
                self.sprite_frames[{"idle": 0, "left": 1, "right": 2, "both": 3}[state]])
            sprite_left = (self.width - sprite.width) // 2 + self.sprite_horizontal_offset()
            sprite_top = self.height - self.sprite_bottom_gap() - sprite.height
            pixel_x, pixel_y = event_x - sprite_left, event_y - sprite_top
            if 0 <= pixel_x < sprite.width and 0 <= pixel_y < sprite.height:
                on_cat = sprite.getpixel((pixel_x, pixel_y))[3] > 32
        self.drag_on_cat = on_cat
        self.drag_has_moved = False

    def finish_drag(self, event=None):
        if getattr(self, "drag_on_cat", False) and not getattr(self, "drag_has_moved", False):
            self.play_cat_tap()
        self.drag_on_cat = False

    def play_cat_tap(self):
        self.love_animation_until = time.monotonic() + 1.8
        center_x = self.width // 2
        center_y = self.height - self.sprite_bottom_gap() - self.sprite_height / 2
        min_x = max(16, center_x - int(self.sprite_width * 0.45))
        max_x = min(self.width - 16, center_x + int(self.sprite_width * 0.45))
        min_y = max(28, center_y - int(self.sprite_height * 0.28))
        max_y = min(self.height - 42, center_y + int(self.sprite_height * 0.28))
        active_positions = []
        now = time.monotonic()
        for started, old_x, old_y in self.floating_hearts:
            age = now - started
            if age < self.heart_travel_time(old_y):
                old_progress = min(1.0, age / self.heart_travel_time(old_y))
                old_y_now = old_y - (old_y - 20) * (1 - (1 - old_progress) ** 2)
                active_positions.append((old_x, old_y_now))

        candidates = []
        for _ in range(100):
            candidate = (random.uniform(min_x, max_x), random.uniform(min_y, max_y))
            spacing = min((math.dist(candidate, position) for position in active_positions),
                          default=1000)
            candidates.append((spacing, candidate))
            if spacing >= 46:
                break
        _, (heart_x, heart_y) = max(candidates, key=lambda item: item[0])
        self.floating_hearts.append((now, heart_x, heart_y))
        self.heart_serial += 1

    def available_skins(self):
        if self.available_skin_cache is not None:
            return self.available_skin_cache
        skins_folder = os.path.join(FOLDER, "Bongo_sprites")
        self.available_skin_cache = sorted(name for name in os.listdir(skins_folder)
                                           if os.path.isdir(os.path.join(skins_folder, name)))
        return self.available_skin_cache

    def available_hats(self):
        if self.available_hat_cache is not None:
            return self.available_hat_cache
        hats_folder = os.path.join(FOLDER, "Bongo_hats")
        if not os.path.isdir(hats_folder):
            self.available_hat_cache = []
        else:
            self.available_hat_cache = sorted(os.path.splitext(name)[0] for name in os.listdir(hats_folder)
                                              if name.lower().endswith(".png"))
        return self.available_hat_cache

    def locked_cosmetics(self):
        return ([ ("skin", name) for name in self.available_skins()
                  if not UNLOCK_ALL_ITEMS and name not in self.unlocked_skins]
                + [("hat", name) for name in self.available_hats()
                   if not UNLOCK_ALL_ITEMS and name not in self.unlocked_hats])

    def chest_bubble_layout(self, physical_width):
        if (not self.pending_chests or self.total_presses < 1000
                or not self.locked_cosmetics()):
            return None
        text = "1 000"
        font = ImageFont.truetype(r"C:\Windows\Fonts\segoeuib.ttf", 12)
        scratch = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
        text_width = scratch.textbbox((0, 0), text, font=font)[2]
        bubble_width = max(68, text_width + 42)
        bubble_x = max(8, min(physical_width - bubble_width - 8,
                              physical_width // 2 + 14))
        return text, bubble_x, self.HEART_TOP_PADDING + 8, bubble_width, text_width

    def update_chest_progress(self):
        now = time.monotonic()
        elapsed = max(0.0, now - self.chest_last_tick)
        self.chest_last_tick = now
        self.chest_progress += elapsed
        while self.chest_progress >= self.chest_interval:
            self.chest_progress -= self.chest_interval
            self.pending_chests += 1
        if now - self.chest_last_save >= 60:
            self.chest_last_save = now
            self.schedule_save()
        self.refresh_chest_widgets()

    def refresh_chest_widgets(self):
        status = getattr(self, "chest_status_widget", None)
        progress_bar = getattr(self, "chest_progress_widget", None)
        button_widget = getattr(self, "chest_open_widget", None)
        if not status or not progress_bar or not button_widget:
            return
        try:
            if (not status.winfo_exists() or not progress_bar.winfo_exists()
                    or not button_widget.winfo_exists()):
                return
            if self.unlock_message and time.monotonic() < self.unlock_message_until:
                text = self.unlock_message
            elif not self.locked_cosmetics():
                text = "Alles freigeschaltet"
            elif self.pending_chests:
                missing = max(0, 1000 - self.total_presses)
                text = ("Truhe bereit" if missing == 0
                        else f"Truhe bereit · noch {missing} Taps")
            else:
                remaining = max(0, int(self.chest_interval - self.chest_progress))
                text = f"Nächste Truhe in {remaining // 60:02d}:{remaining % 60:02d}"
            if getattr(self, "_last_chest_status", None) != text:
                status.configure(text=text)
                self._last_chest_status = text
            time_fraction = (1.0 if self.pending_chests else
                             min(1.0, self.chest_progress / self.chest_interval))
            tap_fraction = min(1.0, max(0.0, self.total_presses / 1000))
            enabled = bool(self.pending_chests and self.total_presses >= 1000
                           and self.locked_cosmetics())
            state = (round(time_fraction * 100), round(tap_fraction * 100), enabled)
            if getattr(self, "_last_chest_bar_state", None) != state:
                self._last_chest_bar_state = state
                progress_bar.delete("all")
                bar_width = max(1, progress_bar.winfo_width())
                bar_height = max(1, progress_bar.winfo_height())
                progress_bar.create_rectangle(0, 0, bar_width, bar_height,
                                              fill="#f2dfbe", outline="")
                progress_bar.create_rectangle(0, 0, bar_width * time_fraction, bar_height,
                                              fill="#f1a947", outline="")
                button_widget.delete("all")
                button_width = max(1, button_widget.winfo_width())
                button_height = max(1, button_widget.winfo_height())
                button_bg = "#fff4dd" if enabled else "#e8e3da"
                button_widget.create_rectangle(0, 0, button_width, button_height,
                                               fill=button_bg, outline="#8b5736")
                button_widget.create_rectangle(1, 1, 1 + (button_width - 2) * tap_fraction,
                                               button_height - 1, fill="#ffc56e", outline="")
                button_widget.create_text(button_width // 2, button_height // 2,
                                          text="Truhe · 1.000 Taps",
                                          fill="#67432e" if enabled else "#8b8175",
                                          font=("Segoe UI Semibold", 8))
        except tk.TclError:
            pass

    def chest_canvas_configured(self, event=None):
        self._last_chest_bar_state = None
        self.root.after_idle(self.refresh_chest_widgets)

    def open_cosmetic_chest(self, tab_name):
        candidates = self.locked_cosmetics()
        if not self.pending_chests or self.total_presses < 1000 or not candidates:
            return
        kind, name = random.choice(candidates)
        self.total_presses -= 1000
        self.pending_chests -= 1
        if kind == "skin":
            self.unlocked_skins.add(name)
            self.unlock_message = f"Skin freigeschaltet: {name}"
        else:
            self.unlocked_hats.add(name)
            self.unlock_message = f"Hut freigeschaltet: {name}"
        now = time.monotonic()
        self.chest_animation_started = now
        self.chest_animation_until = now + 1.7
        self.chest_reward_started = now
        self.chest_reward_until = now + 4.0
        self.chest_reward_kind = kind
        self.chest_reward_name = name
        self.chest_reward_preview = None
        if kind == "skin":
            preview_path = os.path.join(FOLDER, "Bongo_sprites", name, "idle.png")
        else:
            preview_path = os.path.join(FOLDER, "Bongo_hats", f"{name}.png")
        try:
            with Image.open(preview_path) as preview_file:
                self.chest_reward_preview = ImageOps.contain(
                    preview_file.convert("RGBA"), (54, 44), Image.Resampling.LANCZOS)
        except OSError:
            pass
        self.chest_bubble_text = ""
        self.chest_bubble_until = 0.0
        self.unlock_message_until = time.monotonic() + 5
        self.save_counter()
        self.update_display()
        self.refresh_chest_widgets()
        self.menu_tab = tab_name

    def do_drag(self, event):
        if (abs(event.x_root - self.drag_start_pointer[0]) > 4
                or abs(event.y_root - self.drag_start_pointer[1]) > 4):
            self.drag_has_moved = True
        if not self.is_ghost:
            new_x = self.drag_window_position[0] + event.x_root - self.drag_start_pointer[0]
            new_y = self.drag_window_position[1] + event.y_root - self.drag_start_pointer[1]
            self.window_x, self.window_y = new_x, new_y
            self.root.geometry(f"+{new_x}+{new_y}")
            self.position_settings_window()
            self.schedule_save()
            overlay_height = (self.width if self.cat_rotation % 2 else self.height)
            self.layered_api.SetWindowPos(
                self.overlay_hwnd, ctypes.c_void_p(-1),
                new_x - self.overlay_horizontal_padding,
                new_y - self.HEART_TOP_PADDING,
                self.overlay_render_width, overlay_height + self.HEART_TOP_PADDING,
                0x0040 | 0x0010 | 0x0200
            )
            self.last_overlay_position = (
                new_x - self.overlay_horizontal_padding,
                new_y - self.HEART_TOP_PADDING)

    def toggle_ghost_mode(self, event=None):
        if not self.is_ghost:
            self.is_ghost = True
            self.root.attributes("-alpha", 0.35)
            self.set_click_through(True)
            self.root.after(4000, self.reset_ghost_mode)

    def reset_ghost_mode(self):
        self.set_click_through(False)
        self.root.attributes("-alpha", 1.0)
        self.is_ghost = False

    def set_click_through(self, enable):
        try:
            ex_style = self.overlay_get_style(self.root_hwnd, -20)
            if enable:
                ex_style |= 0x00080020
            else:
                ex_style &= ~0x00000020
            self.overlay_set_style(self.root_hwnd, -20, ex_style)
            overlay_style = self.overlay_get_style(self.overlay_hwnd, -20)
            if enable:
                overlay_style |= 0x00000020
            else:
                overlay_style &= ~0x00000020
            self.overlay_set_style(self.overlay_hwnd, -20, overlay_style)
        except Exception:
            pass

    def open_settings_menu(self, event=None):
        if getattr(self, "settings_window", None) and self.settings_window.winfo_exists():
            self.settings_window.destroy()
            return

        window = tk.Toplevel(self.root)
        self.settings_window = window
        window.overrideredirect(True)
        window.attributes("-topmost", True)
        window.configure(bg="#8b5736")
        panel = tk.Frame(window, bg="#fff4dd", padx=10, pady=9,
                         highlightthickness=1, highlightbackground="#67432e")
        panel.pack(fill="both", expand=True, padx=1, pady=1)

        header = tk.Frame(panel, bg="#ffc56e")
        header.pack(fill="x", pady=(0, 8))
        tk.Label(header, text="Bongo Cat", bg="#ffc56e", fg="#67432e",
                 font=("Segoe UI Semibold", 12)).pack(side="left")
        self.menu_taps_label = tk.Label(
            header, text=f"{self.total_presses:,} Taps".replace(",", " "),
            bg="#ffc56e", fg="#67432e", font=("Segoe UI", 9))
        self.menu_taps_label.pack(side="left", padx=12)
        tk.Button(header, text="×", command=window.destroy, bg="#fff4dd", fg="#67432e",
                  activebackground="#ffd073", relief="flat", bd=0,
                  font=("Segoe UI", 11), padx=5, pady=0).pack(side="right")

        tabs = tk.Frame(panel, bg="#8b5736", padx=1, pady=1)
        tabs.pack(fill="x")
        content = tk.Frame(panel, bg="#fffaf0", height=180,
                           highlightthickness=1, highlightbackground="#67432e")
        content.pack(fill="both", expand=True, pady=(7, 0))
        content.pack_propagate(False)
        self.menu_tab = getattr(self, "menu_tab", "Skins")
        self.menu_search_queries = getattr(self, "menu_search_queries", {})
        self.menu_search_job = None
        tab_buttons = {}

        def button(parent, label, command, **options):
            return tk.Button(parent, text=label, command=command,
                             bg="#ffe5b4", fg="#67432e", activebackground="#ffc56e",
                             activeforeground="#67432e", relief="flat", bd=0,
                             font=("Segoe UI", 9), padx=8, pady=5, **options)

        def add_chest_row(parent, tab_name):
            row = tk.Frame(parent, bg="#fff4dd")
            row.pack(fill="x", padx=8, pady=(0, 3))
            details = tk.Frame(row, bg="#fff4dd")
            details.pack(side="left", fill="x", expand=True, padx=(0, 8))
            status = tk.Label(details, text="", bg="#fff4dd", fg="#67432e",
                              font=("Segoe UI", 8), anchor="w")
            status.pack(fill="x")
            progress_bar = tk.Canvas(details, width=200, height=8, bg="#fff4dd",
                                     highlightthickness=0, bd=0)
            progress_bar.pack(fill="x", pady=(3, 0))
            chest_button = tk.Canvas(row, width=142, height=27, bg="#fff4dd",
                                     highlightthickness=0, bd=0, cursor="hand2")
            chest_button.pack(side="right")
            chest_button.bind("<Button-1>", lambda _event: (
                self.open_cosmetic_chest(tab_name), show_tab(tab_name)))
            progress_bar.bind("<Configure>", self.chest_canvas_configured)
            chest_button.bind("<Configure>", self.chest_canvas_configured)
            row.update_idletasks()
            self.chest_status_widget = status
            self.chest_progress_widget = progress_bar
            self.chest_open_widget = chest_button
            self._last_chest_status = None
            self._last_chest_bar_state = None
            self.refresh_chest_widgets()

        def add_search_row(parent, tab_name, placeholder):
            row = tk.Frame(parent, bg="#fff4dd")
            row.pack(fill="x", padx=8, pady=(0, 4))
            tk.Label(row, text="Suche", bg="#fff4dd", fg="#67432e",
                     font=("Segoe UI", 8)).pack(side="left", padx=(0, 5))
            query_var = tk.StringVar(window, value=self.menu_search_queries.get(tab_name, ""))
            search = tk.Entry(row, textvariable=query_var, font=("Segoe UI", 9),
                              relief="solid", bd=1)
            search.pack(side="left", fill="x", expand=True)
            search.configure(fg="#67432e")
            self.menu_search_entry = search
            search.bind("<Return>", lambda _event: show_tab(tab_name))

            def search_changed(*_args):
                self.menu_search_queries[tab_name] = query_var.get()
                if self.menu_search_job is not None:
                    try:
                        window.after_cancel(self.menu_search_job)
                    except tk.TclError:
                        pass
                self.menu_search_job = window.after(220, lambda: show_tab(tab_name))

            query_var.trace_add("write", search_changed)

        def scrollable_grid(parent):
            area = tk.Frame(parent, bg="#fffaf0")
            area.pack(fill="both", expand=True, padx=4, pady=3)
            view = tk.Canvas(area, bg="#fffaf0", highlightthickness=0, height=130)
            scrollbar = tk.Scrollbar(area, orient="vertical", command=view.yview)
            view.configure(yscrollcommand=scrollbar.set)
            scrollbar.pack(side="right", fill="y")
            view.pack(side="left", fill="both", expand=True)
            grid = tk.Frame(view, bg="#fffaf0")
            grid_id = view.create_window((0, 0), window=grid, anchor="nw")
            grid.bind("<Configure>", lambda _event: view.configure(scrollregion=view.bbox("all")))
            view.bind("<Configure>", lambda event: view.itemconfigure(grid_id, width=event.width))
            bind_wheel(view, view)
            self.menu_scroll_view = view
            return view, grid

        def bind_wheel(widget, view):
            def scroll(event):
                direction = -1 if event.delta > 0 else 1
                steps = max(1, round(abs(event.delta) / 120 * 4))
                view.yview_scroll(direction * steps, "units")
                return "break"

            widget.bind("<MouseWheel>", scroll)

        def cached_icon(path, grayscale=False):
            key = (os.path.abspath(path), bool(grayscale))
            icon = self.inventory_thumbnail_cache.get(key)
            if icon is not None:
                return icon
            with Image.open(path) as source_image:
                thumbnail = source_image.convert("RGBA")
                if grayscale:
                    alpha = thumbnail.getchannel("A")
                    thumbnail = ImageOps.grayscale(thumbnail).convert("RGBA")
                    thumbnail.putalpha(alpha)
                thumbnail.thumbnail((64, 56), Image.Resampling.LANCZOS)
            icon = ImageTk.PhotoImage(thumbnail, master=window)
            self.inventory_thumbnail_cache[key] = icon
            return icon

        def preload_hat_icons(index=0):
            """Warm the shared hat thumbnail cache without blocking the UI."""
            names = self.available_hats()
            batch_end = min(index + 4, len(names))
            for name in names[index:batch_end]:
                try:
                    cached_icon(os.path.join(FOLDER, "Bongo_hats", f"{name}.png"))
                except (OSError, tk.TclError):
                    pass
            if batch_end < len(names) and window.winfo_exists():
                window.after(1, lambda: preload_hat_icons(batch_end))

        def show_tab(tab_name):
            previous_tab = getattr(self, "menu_tab", tab_name)
            previous_view = getattr(self, "menu_scroll_view", None)
            if previous_view is not None:
                try:
                    if previous_view.winfo_exists():
                        self.menu_scroll_positions[previous_tab] = previous_view.yview()[0]
                except tk.TclError:
                    pass
            self.menu_scroll_view = None
            self.menu_tab = tab_name
            self.menu_item_tiles = {}
            self.dex_stats_label = None
            self.chest_status_widget = None
            self.chest_progress_widget = None
            self.chest_open_widget = None
            for name, tab_button in tab_buttons.items():
                tab_button.configure(bg="#ffc56e" if name == tab_name else "#ffe5b4")
            for child in content.winfo_children():
                child.destroy()

            if tab_name == "Skins":
                tk.Label(content, text="SKINS", bg="#fff4dd", fg="#67432e",
                         font=("Segoe UI Semibold", 9)).pack(anchor="w", padx=10, pady=(6, 2))
                add_search_row(content, tab_name, "Skin suchen")
                add_chest_row(content, tab_name)
                view, grid = scrollable_grid(content)
                skin_names = sorted(
                    (name for name in self.available_skins()
                     if name in self.unlocked_skins
                     and self.menu_search_queries.get(tab_name, "").casefold() in name.casefold()),
                    key=str.lower)
                self.inventory_icons = []
                for index, skin_name in enumerate(skin_names):
                    selected = skin_name == self.current_skin
                    tile_bg = "#ffe2a6" if selected else "#fffaf0"
                    tile = tk.Frame(grid, bg=tile_bg,
                                    highlightthickness=2 if selected else 1,
                                    highlightbackground="#e8973e" if selected else "#d8bd93",
                                    padx=5, pady=5, width=96, height=100)
                    tile.grid(row=index // 3, column=index % 3, padx=3, pady=3)
                    self.menu_item_tiles[("skin", skin_name)] = tile
                    tile.pack_propagate(False)
                    try:
                        idle_path = os.path.join(FOLDER, "Bongo_sprites", skin_name, "idle.png")
                        icon = cached_icon(idle_path)
                        self.inventory_icons.append(icon)
                        icon_label = tk.Label(tile, image=icon, bg=tile["bg"], bd=0)
                    except (OSError, tk.TclError):
                        icon_label = tk.Label(tile, text="Bongo Cat", bg=tile["bg"],
                                              fg="#67432e", font=("Segoe UI", 9))
                    icon_label.pack(expand=True, pady=(3, 1))
                    tk.Label(tile, text=skin_name, bg=tile["bg"],
                             fg="#67432e",
                             font=("Segoe UI Semibold", 9)).pack()
                    select = (lambda _event=None, name=skin_name:
                              (self.select_skin(name), self.refresh_inventory_selection("skin", name)))
                    tile.bind("<Button-1>", select)
                    for child in tile.winfo_children():
                        child.bind("<Button-1>", select)
                        bind_wheel(child, view)
                    bind_wheel(tile, view)
            elif tab_name == "H\u00fcte":
                tk.Label(content, text="H\u00dcTE", bg="#fff4dd", fg="#67432e",
                         font=("Segoe UI Semibold", 9)).pack(anchor="w", padx=10, pady=(6, 2))
                add_search_row(content, tab_name, "Hut suchen")
                add_chest_row(content, tab_name)
                view, grid = scrollable_grid(content)
                hats_folder = os.path.join(FOLDER, "Bongo_hats")
                os.makedirs(hats_folder, exist_ok=True)
                hat_names = [name for name in self.available_hats()
                             if name in self.unlocked_hats
                             and self.menu_search_queries.get(tab_name, "").casefold() in name.casefold()]
                query_active = bool(self.menu_search_queries.get(tab_name, "").strip())
                visible_hat_names = hat_names if query_active else hat_names[:self.hat_visible_limit]
                self.inventory_icons = []
                hat_options = [(None, None)] + [
                    (name, f"{name}.png")
                    for name in visible_hat_names
                ]
                hat_options.sort(key=lambda item: (
                    (item[0] or "").lower()))
                for index, (hat_name, filename) in enumerate(hat_options):
                    selected = hat_name == self.current_hat
                    tile_bg = "#ffe2a6" if selected else "#fffaf0"
                    tile = tk.Frame(grid, bg=tile_bg,
                                    highlightthickness=2 if selected else 1,
                                    highlightbackground="#e8973e" if selected else "#d8bd93",
                                    padx=5, pady=5, width=96, height=100)
                    tile.grid(row=index // 3, column=index % 3, padx=3, pady=3)
                    self.menu_item_tiles[("hat", hat_name)] = tile
                    tile.pack_propagate(False)
                    if filename:
                        try:
                            icon = cached_icon(os.path.join(hats_folder, filename))
                            self.inventory_icons.append(icon)
                            icon_label = tk.Label(tile, image=icon, bg=tile["bg"], bd=0)
                        except (OSError, tk.TclError):
                            icon_label = tk.Label(tile, text=hat_name, bg=tile["bg"],
                                                  fg="#67432e", font=("Segoe UI", 9))
                    else:
                        icon_label = tk.Label(tile, text="✕", bg=tile["bg"], fg="#67432e",
                                              font=("Segoe UI", 26))
                    icon_label.pack(expand=True, pady=(3, 1))
                    visible_name = hat_name or "Kein Hut"
                    tk.Label(tile, text=visible_name, bg=tile["bg"],
                             fg="#67432e",
                             font=("Segoe UI Semibold", 9)).pack()
                    select = (lambda _event=None, name=hat_name:
                              (self.select_hat(name), self.refresh_inventory_selection("hat", name)))
                    tile.bind("<Button-1>", select)
                    for child in tile.winfo_children():
                        child.bind("<Button-1>", select)
                        bind_wheel(child, view)
                    bind_wheel(tile, view)
                if len(hat_names) > len(visible_hat_names):
                    more = button(grid,
                                  f"Weitere {min(DEX_PAGE_SIZE, len(hat_names) - len(visible_hat_names))} laden",
                                  lambda: (setattr(self, "hat_visible_limit",
                                                   self.hat_visible_limit + DEX_PAGE_SIZE),
                                           show_tab("H\u00fcte")))
                    more.grid(row=(len(hat_options) + 2) // 3, column=0,
                              columnspan=3, sticky="ew", padx=3, pady=6)
            elif tab_name == "Bongo Dex":
                tk.Label(content, text="Bongo Dex", bg="#fff4dd", fg="#67432e",
                         font=("Segoe UI Semibold", 10)).pack(anchor="w", padx=8, pady=(5, 2))
                locked_skins = [name for name in self.available_skins()
                                if name not in self.unlocked_skins]
                locked_hats = [name for name in self.available_hats()
                               if name not in self.unlocked_hats]
                total_items = len(self.available_skins()) + len(self.available_hats())
                unlocked_count = len(self.unlocked_skins) + len(self.unlocked_hats)
                self.dex_stats_label = tk.Label(
                    content,
                    text=f"Freigeschaltet: {unlocked_count}/{total_items}  •  "
                         f"{self.total_presses:,} Taps".replace(",", " "),
                    bg="#fff4dd", fg="#67432e", font=("Segoe UI", 8))
                self.dex_stats_label.pack(anchor="w", padx=8, pady=(0, 3))
                view, grid = scrollable_grid(content)
                self.inventory_icons = []
                dex_items = [("skin", name) for name in locked_skins]
                dex_items += [("hat", name) for name in locked_hats]
                visible_items = dex_items[:self.dex_visible_limit]
                for index, (kind, item_name) in enumerate(visible_items):
                    tile = tk.Frame(grid, bg="#e5e3df", highlightthickness=1,
                                    highlightbackground="#c5c1bb", padx=5, pady=5,
                                    width=96, height=100)
                    tile.grid(row=index // 3, column=index % 3, padx=3, pady=3)
                    tile.pack_propagate(False)
                    item_path = (os.path.join(FOLDER, "Bongo_sprites", item_name, "idle.png")
                                 if kind == "skin" else
                                 os.path.join(FOLDER, "Bongo_hats", f"{item_name}.png"))
                    try:
                        icon = cached_icon(item_path, grayscale=True)
                        self.inventory_icons.append(icon)
                        icon_label = tk.Label(tile, image=icon, bg=tile["bg"], bd=0)
                    except (OSError, tk.TclError):
                        icon_label = tk.Label(tile, text="LOCK", bg=tile["bg"],
                                              fg="#827e77", font=("Segoe UI", 8))
                    icon_label.pack(expand=True, pady=(2, 1))
                    tk.Label(tile, text=item_name, bg=tile["bg"], fg="#827e77",
                             font=("Segoe UI Semibold", 8)).pack()
                    tk.Label(tile, text="Skin" if kind == "skin" else "Hut",
                             bg=tile["bg"], fg="#827e77",
                             font=("Segoe UI", 7)).pack()
                    bind_wheel(tile, view)
                    for child in tile.winfo_children():
                        bind_wheel(child, view)
                if not dex_items:
                    tk.Label(grid, text="Alles freigeschaltet", bg="#fffaf0",
                             fg="#67432e", font=("Segoe UI", 9)).grid(
                                 row=0, column=0, padx=8, pady=10)
                elif len(dex_items) > len(visible_items):
                    more = button(grid,
                                  f"Weitere {min(DEX_PAGE_SIZE, len(dex_items) - len(visible_items))} laden",
                                  lambda: (setattr(self, "dex_visible_limit",
                                                   self.dex_visible_limit + DEX_PAGE_SIZE),
                                           show_tab("Bongo Dex")))
                    more.grid(row=(len(visible_items) + 2) // 3, column=0,
                              columnspan=3, sticky="ew", padx=3, pady=6)
            elif tab_name == "Multiplayer":
                tk.Label(content, text="Multiplayer", bg="#fff4dd", fg="#67432e",
                         font=("Segoe UI Semibold", 10)).pack(anchor="w", padx=8, pady=(7, 6))
                tk.Label(content, text="●  Nicht verbunden", bg="#fff4dd", fg="#67432e",
                         font=("Segoe UI", 9)).pack(anchor="w", padx=8)
                tk.Label(content, text="Der lokale Bongo Cat hat derzeit keine Online-Verbindung.",
                         bg="#fff4dd", fg="#67432e", font=("Segoe UI", 9),
                         wraplength=320, justify="left").pack(anchor="w", padx=8, pady=6)

            elif tab_name == "+":
                tk.Label(content, text="Importieren", bg="#fff4dd", fg="#67432e",
                    font=("Segoe UI Semibold", 10)).pack(anchor="w", padx=8, pady=(7, 6))
                select_label = tk.Label(content, text="Bitte wähle eine .zip Datei aus die ein Skinset oder Hatpack enthält",
                    bg="#fff4dd", fg="#67432e", font=("Segoe UI", 9),
                    wraplength=320, justify="left")
                select_label.pack(anchor="w", padx=8, pady=6)

                zip_file = filename = filedialog.askopenfilename(initialdir="/", title="Bitte wähle eine .zip Datei aus die ein skinset enthält", filetypes=(("Zip Dateien", "*.zip"),("Alle Dateien", "*.*")))

                if not zip_file:
                    select_label.configure(text="Importieren abgebrochen")
                else:
                    extract_folder = os.path.join(FOLDER, "__extract__")
                    import_sprite_folder = os.path.join(FOLDER,"Bongo_sprites")
                    import_hat_folder = os.path.join(FOLDER,"Bongo_hats")
                    self.unzip(zip_file, extract_folder)

                    extract_folder = os.path.join(extract_folder, os.path.splitext(os.path.basename(zip_file))[0])

                    type_file = os.path.join(extract_folder, "skinpack.cnfg")

                    if os.path.exists(type_file):
                        os.remove(type_file)

                        unlocked_skins = 0
                        not_unlocked_skins = 0
                        skipped_skins = 0

                        new_skins_text = None

                        for name in os.listdir(extract_folder):
                            full_path = os.path.join(extract_folder, name)

                            if os.path.isdir(full_path):
                                targ_folder_name = os.path.join(import_sprite_folder, os.path.basename(full_path))

                                if os.path.exists(targ_folder_name):
                                    skipped_skins += 1
                                    continue

                                unlock_file = os.path.join(full_path, "skin_default_unlock.cnfg")
                                if os.path.exists(unlock_file):
                                    self.unlocked_skins.add(name)
                                    unlocked_skins += 1

                                    os.remove(unlock_file)

                                else:
                                    not_unlocked_skins +=1

                                if new_skins_text == None:
                                    new_skins_text = name
                                else:
                                    new_skins_text = f"{new_skins_text}, {name}"

                                shutil.move(full_path, import_sprite_folder)

                        shutil.rmtree(extract_folder)

                        total_skins = unlocked_skins+not_unlocked_skins

                        if total_skins == 0:
                            if skipped_skins > 0:
                                select_label.configure(text="Du hast bereits alle Skins in diesem Skinpack")
                            else:
                                select_label.configure(text="Fehler: Es wurden keine Skins Importiert")
                        else:
                            self.available_skin_cache = None

                            if total_skins != 1:
                                select_label.configure(text=f"{total_skins} Skins erfolgreich hinzugefügt")
                            else:
                                select_label.configure(text="1 Skins erfolgreich hinzugefügt")

                            if unlocked_skins != 1:
                                label_text = f"{unlocked_skins} Skins davon wurden bereits für dich freigeschaltet"
                            else:
                                label_text = "1 Skin davon wurde bereits für dich freigeschaltet"

                            tk.Label(content, text=label_text,
                                bg="#fff4dd", fg="#67432e", font=("Segoe UI", 9),
                                wraplength=320, justify="left").pack(anchor="w", padx=8, pady=6)
                                
                            tk.Label(content, text=f"Neue Skins: {new_skins_text}",
                                bg="#fff4dd", fg="#67432e", font=("Segoe UI", 9),
                                wraplength=320, justify="left").pack(anchor="w", padx=8, pady=6)

                    else:
                        type_file = os.path.join(extract_folder, "hatpack.cnfg")

                        new_hats_text = None

                        skipped_hats = 0
                        unlocked_hats = 0
                        total_hats = 0

                        with open(type_file) as file:
                            for line in file:
                                unlocked_hats += 1
                                self.unlocked_hats.add(line.strip())

                        os.remove(type_file)

                        for name in os.listdir(extract_folder):
                            full_path = os.path.join(extract_folder, name)

                            targ_folder_name = os.path.join(import_hat_folder, os.path.basename(full_path))
                            
                            if os.path.exists(targ_folder_name):
                                skipped_hats += 1
                                continue

                            if new_hats_text == None:
                                new_hats_text = os.path.splitext(name)[0]
                            else:
                                new_hats_text = f"{new_hats_text}, {os.path.splitext(name)[0]}"
                            
                            shutil.move(full_path, import_hat_folder)
                            total_hats +=1

                        shutil.rmtree(extract_folder)

                        if total_hats == 0:
                            if skipped_hats > 0:
                                select_label.configure(text="Du hast bereits alle Hüte in diesem Hatpack")
                            else:
                                select_label.configure(text="Fehler: Es wurden keine Hüte Importiert")
                        else:
                            self.available_hat_cache = None
                        
                            if total_hats != 1:
                                select_label.configure(text=f"{total_hats} Hüte erfolgreich hinzugefügt")
                            else:
                                select_label.configure(text="1 Hut erfolgreich hinzugefügt")
                        
                            if unlocked_hats != 1:
                                label_text = f"{unlocked_skins} Hüte davon wurden bereits für dich freigeschaltet"
                            else:
                                label_text = "1 Hut davon wurde bereits für dich freigeschaltet"
                        
                            tk.Label(content, text=label_text,
                                bg="#fff4dd", fg="#67432e", font=("Segoe UI", 9),
                                wraplength=320, justify="left").pack(anchor="w", padx=8, pady=6)
                                                        
                            tk.Label(content, text=f"Neue Hüte: {new_hats_text}",
                                bg="#fff4dd", fg="#67432e", font=("Segoe UI", 9),
                                wraplength=320, justify="left").pack(anchor="w", padx=8, pady=6)

            else:
                tk.Label(content, text="Anzeige", bg="#fff4dd", fg="#67432e",
                         font=("Segoe UI Semibold", 10)).pack(anchor="w", padx=8, pady=(7, 4))
                size_options = (("Klein", 0.3), ("Normal", 0.45), ("Gro\u00df", 0.55))
                sizes = tk.Frame(content, bg="#fff4dd")
                sizes.pack(fill="x", padx=5)
                for label, factor in size_options:
                    size_button = button(
                        sizes, label,
                        lambda value=factor: (window.destroy(), self.change_scale(value)))
                    if math.isclose(self.scale_factor, factor):
                        size_button.configure(bg="#ffc56e")
                    size_button.pack(side="left", expand=True, fill="x", padx=2)

                self.autostart_var = tk.BooleanVar(
                    value=self.get_startup_shortcut_path().exists()
                )
                
                self.autostart_checkbtn = tk.Checkbutton(
                    content,
                    text="Windows-Autostart",
                    variable=self.autostart_var,
                    command=lambda: self.set_autostart(
                    self.autostart_var.get()
                    ),
                    bg="#fff4dd",
                    fg="#67432e",
                    activebackground="#fff4dd",
                    activeforeground="#67432e",
                    selectcolor="#ffc56e",
                    font=("Segoe UI", 9),
                    relief="flat",
                    bd=0,
                    highlightthickness=0,
                    padx=4,
                    pady=5,
                    cursor="hand2"
                )
                self.autostart_checkbtn.pack(
                    anchor="w",
                    padx=7,
                    pady=(3, 0)
                )

                self.cheats_var = tk.BooleanVar(
                    value=UNLOCK_ALL_ITEMS
                )
                                
                self.cheats_checkbtn = tk.Checkbutton(
                    content,
                    text="Alles Freischalten",
                    variable=self.cheats_var,
                    command=lambda: self.set_cheats(),
                    bg="#fff4dd",
                    fg="#67432e",
                    activebackground="#fff4dd",
                    activeforeground="#67432e",
                    selectcolor="#ffc56e",
                    font=("Segoe UI", 9),
                    relief="flat",
                    bd=0,
                    highlightthickness=0,
                    padx=4,
                    pady=5,
                    cursor="hand2"
                )
                self.cheats_checkbtn.pack(
                    anchor="w",
                    padx=7,
                    pady=(3, 0)
                )
                
                button(content, "Z\u00e4hler zur\u00fccksetzen",
                       lambda: (self.reset_counter(), show_tab("Settings")), anchor="w").pack(fill="x", padx=7, pady=(7, 0))
                button(content, "Beenden", self.close, anchor="w").pack(fill="x", padx=7)


            current_view = getattr(self, "menu_scroll_view", None)
            if current_view is not None:
                position = self.menu_scroll_positions.get(tab_name, 0.0)
                window.after_idle(lambda view=current_view, y=position: (
                    view.yview_moveto(y) if view.winfo_exists() else None))
            if tab_name in ("Skins", "H\u00fcte"):
                search_entry = getattr(self, "menu_search_entry", None)
                if search_entry is not None:
                    window.after_idle(lambda entry=search_entry: (
                        (entry.focus_force(), entry.icursor("end"))
                        if entry.winfo_exists() else None))
        for tab_name in ("Skins", "H\u00fcte", "Bongo Dex", "Multiplayer", "Settings", "+"):
            tab_button = button(tabs, tab_name, lambda name=tab_name: show_tab(name))
            tab_button.pack(side="left", padx=(0, 1))
            tab_buttons[tab_name] = tab_button
        if self.menu_tab not in tab_buttons:
            self.menu_tab = "Skins"
        show_tab(self.menu_tab)
        search_entry = getattr(self, "menu_search_entry", None)
        if search_entry is not None and search_entry.winfo_exists():
            window.after_idle(search_entry.focus_set)
        window.after(50, preload_hat_icons)

        window.update_idletasks()
        popup_w, popup_h = 390, 295
        self.settings_window_size = (popup_w, popup_h)
        window.geometry(f"{popup_w}x{popup_h}+0+0")
        self.position_settings_window()
        window.lift()

    def unzip(self,zip_pfad: str, ziel_ordner: str) -> None:
        """
        Entpackt eine ZIP-Datei in einen Zielordner.
        
        :param zip_pfad: Pfad zur ZIP-Datei
        :param ziel_ordner: Ordner, in den entpackt werden soll
        """
        os.makedirs(ziel_ordner, exist_ok=True)
        
        with zipfile.ZipFile(zip_pfad, 'r') as zip_ref:
            zip_ref.extractall(ziel_ordner)

    def position_settings_window(self):
        window = getattr(self, "settings_window", None)
        if not window or not window.winfo_exists():
            return
        popup_w, popup_h = getattr(
            self, "settings_window_size", (window.winfo_width(), window.winfo_height())
        )
        cat_x, cat_y = self.root.winfo_rootx(), self.root.winfo_rooty()
        cat_w, cat_h = self.root.winfo_width(), self.root.winfo_height()
        x = cat_x + (cat_w - popup_w) // 2
        y = cat_y + cat_h + 8
        if y + popup_h > window.winfo_screenheight():
            y = cat_y - popup_h - 8
        x = max(0, min(x, window.winfo_screenwidth() - popup_w))
        y = max(0, min(y, window.winfo_screenheight() - popup_h))
        window.geometry(f"{popup_w}x{popup_h}+{x}+{y}")

    def refresh_inventory_selection(self, kind, selected_name):
        """Update the selected tile in place without rebuilding the menu."""
        tiles = getattr(self, "menu_item_tiles", {})
        for (tile_kind, name), tile in list(tiles.items()):
            if tile_kind != kind:
                continue
            selected = name == selected_name
            bg = "#ffe2a6" if selected else "#fffaf0"
            try:
                tile.configure(bg=bg, highlightthickness=2 if selected else 1,
                               highlightbackground="#e8973e" if selected else "#d8bd93")
                for child in tile.winfo_children():
                    child.configure(bg=bg)
            except tk.TclError:
                pass

    def select_skin(self, skin_name):
        skin_folder = os.path.join(FOLDER, "Bongo_sprites", skin_name)
        if not os.path.isdir(skin_folder) or skin_name not in self.unlocked_skins:
            return
        self.current_skin = skin_name
        self.folder = skin_folder
        self.load_and_resize_images()
        self.canvas.config(width=self.width, height=self.height)
        self.update_display()
        self.save_counter()

    def select_hat(self, hat_name):
        if hat_name is not None and hat_name not in self.unlocked_hats:
            return
        self.current_hat = hat_name
        self.load_hat_image()
        self.update_display()
        self.save_counter()

    def change_scale(self, factor):
        self.scale_factor = factor
        self.load_and_resize_images()
        self.canvas.config(width=self.width, height=self.height)
        self.update_display()
        self.save_counter()

    def reset_counter(self):
        self.total_presses = 0
        self.save_counter()
        self.update_display()

    def load_counter(self):
        try:
            with open(self.counter_file, "r", encoding="utf-8") as counter_file:
                data = json.load(counter_file)
                saved_skin = data.get("skin", self.current_skin)
                saved_unlocked_skins = data.get("unlocked_skins", ["Default"])
                if isinstance(saved_unlocked_skins, list):
                    available_skins = set(self.available_skins())
                    self.unlocked_skins = {
                        name for name in saved_unlocked_skins
                        if isinstance(name, str) and name in available_skins
                    }
                self.unlocked_skins.add("Default")
                if isinstance(saved_skin, str):
                    saved_skin_folder = os.path.join(FOLDER, "Bongo_sprites", saved_skin)
                    if os.path.isdir(saved_skin_folder):
                        self.current_skin = saved_skin
                        self.folder = saved_skin_folder
                        self.unlocked_skins.add(saved_skin)
                saved_hat = data.get("hat")
                self.current_hat = saved_hat if isinstance(saved_hat, str) else None
                saved_unlocked_hats = data.get("unlocked_hats", [])
                if isinstance(saved_unlocked_hats, list):
                    available_hats = set(self.available_hats())
                    self.unlocked_hats = {
                        name for name in saved_unlocked_hats
                        if isinstance(name, str) and name in available_hats
                    }
                if isinstance(self.current_hat, str):
                    if self.current_hat in self.available_hats():
                        self.unlocked_hats.add(self.current_hat)
                self.cat_rotation = int(data.get("rotation", 0)) % 4
                self.cat_flipped = bool(data.get("flipped", False))
                self.window_x = int(data.get("x", self.window_x))
                self.window_y = int(data.get("y", self.window_y))
                loaded_scale = float(data.get("scale", self.scale_factor))
                if math.isclose(loaded_scale, 0.35):
                    loaded_scale = 0.45
                if any(math.isclose(loaded_scale, value) for value in (0.2, 0.45, 0.55)):
                    self.scale_factor = loaded_scale
                self.pending_chests = max(0, int(data.get("pending_chests", 0)))
                self.chest_progress = max(0.0, float(data.get("chest_progress", 0.0)))
                while self.chest_progress >= self.chest_interval:
                    self.chest_progress -= self.chest_interval
                    self.pending_chests += 1
                return max(0, int(data.get("taps", 0)))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return 0

    def schedule_save(self):
        if self.save_job is None:
            self.save_job = self.root.after(500, self.save_counter)

    def save_counter(self):
        self.save_job = None
        try:
            with open(self.counter_file, "w", encoding="utf-8") as counter_file:
                json.dump({"taps": self.total_presses, "rotation": self.cat_rotation,
                           "flipped": self.cat_flipped,
                           "scale": self.scale_factor,
                           "skin": self.current_skin, "hat": self.current_hat,
                           "unlocked_skins": sorted(self.unlocked_skins),
                           "unlocked_hats": sorted(self.unlocked_hats),
                           "pending_chests": self.pending_chests,
                           "chest_progress": self.chest_progress,
                           "x": self.window_x, "y": self.window_y}, counter_file)
        except OSError as error:
            print(f"Tap-Zähler konnte nicht gespeichert werden: {error}")

    def close(self):
        self.save_counter()
        if self.hook:
            self.user32.UnhookWindowsHookEx(self.hook)
            self.hook = None
        if getattr(self, "mouse_hook", None):
            self.user32.UnhookWindowsHookEx(self.mouse_hook)
            self.mouse_hook = None
        self.root.destroy()


if __name__ == "__main__":
    try:
        create_desktop_shortcut()
    except (OSError, subprocess.SubprocessError) as error:
        print(f"Desktop-Verknüpfung konnte nicht erstellt werden: {error}")
    root = tk.Tk()
    app = BongoCatPro(root)
    root.mainloop()

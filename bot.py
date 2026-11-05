import os
import logging
import tempfile
import zipfile
from pathlib import Path

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ConversationHandler, filters, ContextTypes
)

from pypdf import PdfWriter, PdfReader
import pikepdf
import img2pdf
from pdf2image import convert_from_path
from pdf2docx import Converter
from PIL import Image
from reportlab.pdfgen import canvas as rl_canvas
from reportlab.lib.pagesizes import A4
from reportlab.lib.colors import Color
import pytesseract

# ============================================================
# ⚠️  GANTI TOKEN DI SINI DENGAN TOKEN BARU DARI BOTFATHER!
# ============================================================
TOKEN = os.environ.get("TOKEN", "9011436398:AAGUo10-J44ikHIRmnbiI7_AjheDkX8540M")
# ============================================================

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ========== STATES ==========
MENU, WAIT_FILE, WAIT_MULTI, WAIT_ROT, WAIT_PASS, WAIT_WM = range(6)


# ========== KEYBOARDS ==========

def kb_main():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔀 Merge PDF",      callback_data="merge"),
         InlineKeyboardButton("✂️ Split PDF",       callback_data="split")],
        [InlineKeyboardButton("🗜️ Compress PDF",   callback_data="compress"),
         InlineKeyboardButton("🔄 Rotate PDF",     callback_data="rotate")],
        [InlineKeyboardButton("🖼️ PDF → JPG",      callback_data="pdf_jpg"),
         InlineKeyboardButton("📄 JPG → PDF",      callback_data="jpg_pdf")],
        [InlineKeyboardButton("📝 PDF → Word",     callback_data="pdf_word"),
         InlineKeyboardButton("💧 Watermark",      callback_data="watermark")],
        [InlineKeyboardButton("🔒 Protect PDF",    callback_data="protect"),
         InlineKeyboardButton("🔓 Unlock PDF",     callback_data="unlock")],
        [InlineKeyboardButton("🔢 Nomor Halaman",  callback_data="pagenum"),
         InlineKeyboardButton("🔍 OCR PDF",        callback_data="ocr")],
    ])

def kb_back():
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("◀️ Menu Utama", callback_data="menu")
    ]])

def kb_merge():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Gabungkan Sekarang!", callback_data="do_merge")],
        [InlineKeyboardButton("◀️ Menu Utama",          callback_data="menu")],
    ])

def kb_jpg_pdf():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Buat PDF Sekarang!", callback_data="do_jpg_pdf")],
        [InlineKeyboardButton("◀️ Menu Utama",         callback_data="menu")],
    ])

def kb_rotate():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("↻ 90°",  callback_data="rot_90"),
         InlineKeyboardButton("↻ 180°", callback_data="rot_180"),
         InlineKeyboardButton("↻ 270°", callback_data="rot_270")],
        [InlineKeyboardButton("◀️ Menu Utama", callback_data="menu")],
    ])


# ========== INSTRUKSI TIAP FITUR ==========

FEATURE_INFO = {
    "merge":     ("🔀 *Merge PDF*\n\nKirim file‑file PDF satu per satu.\n"
                  "Klik *Gabungkan* setelah semua terkirim.", WAIT_MULTI),
    "split":     ("✂️ *Split PDF*\n\nKirim file PDF yang ingin dipecah per halaman.", WAIT_FILE),
    "compress":  ("🗜️ *Compress PDF*\n\nKirim file PDF yang ingin dikompres.", WAIT_FILE),
    "rotate":    ("🔄 *Rotate PDF*\n\nKirim file PDF yang ingin diputar.", WAIT_FILE),
    "pdf_jpg":   ("🖼️ *PDF → JPG*\n\nKirim file PDF yang ingin dikonversi ke gambar JPG.", WAIT_FILE),
    "jpg_pdf":   ("📄 *JPG → PDF*\n\nKirim gambar JPG/PNG satu per satu.\n"
                  "Klik *Buat PDF* setelah semua terkirim.", WAIT_MULTI),
    "pdf_word":  ("📝 *PDF → Word*\n\nKirim file PDF yang ingin diubah ke Word (.docx).", WAIT_FILE),
    "watermark": ("💧 *Watermark PDF*\n\nKirim file PDF yang ingin diberi watermark.", WAIT_FILE),
    "protect":   ("🔒 *Protect PDF*\n\nKirim file PDF yang ingin diproteksi dengan password.", WAIT_FILE),
    "unlock":    ("🔓 *Unlock PDF*\n\nKirim file PDF yang ingin dihapus passwordnya.", WAIT_FILE),
    "pagenum":   ("🔢 *Nomor Halaman*\n\nKirim file PDF yang ingin ditambahkan nomor halaman.", WAIT_FILE),
    "ocr":       ("🔍 *OCR PDF*\n\nKirim file PDF scan untuk diekstrak teksnya (Bahasa Indonesia/Inggris).", WAIT_FILE),
}


# ========== UTILITY ==========

def cleanup(ctx: ContextTypes.DEFAULT_TYPE):
    """Hapus semua file temporary milik user."""
    for f in ctx.user_data.get("files", []):
        try: os.unlink(f["path"])
        except: pass
    for key in ("rot_file", "wm_file", "prot_file", "unlk_file"):
        p = ctx.user_data.get(key)
        if p:
            try: os.unlink(p)
            except: pass


async def download_tg_file(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Download file dari Telegram. Return (path, filename) atau None."""
    msg = update.message
    if msg.document:
        tg_obj  = msg.document
        filename = tg_obj.file_name or "file.bin"
    elif msg.photo:
        tg_obj  = msg.photo[-1]
        filename = "photo.jpg"
    else:
        return None

    suffix   = Path(filename).suffix or ".bin"
    bot_file = await ctx.bot.get_file(tg_obj.file_id)

    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        await bot_file.download_to_drive(tmp.name)
        return tmp.name, filename


def get_chat_id(target) -> int:
    """Ambil chat_id dari Update atau CallbackQuery."""
    if hasattr(target, "effective_chat") and target.effective_chat:
        return target.effective_chat.id
    if hasattr(target, "message") and target.message:
        return target.message.chat_id
    raise ValueError("Tidak bisa menentukan chat_id")


async def send_result(target, ctx: ContextTypes.DEFAULT_TYPE,
                      path: str, filename: str, caption: str = "✅ Selesai!"):
    """Kirim file hasil ke user."""
    chat_id = get_chat_id(target)

    if os.path.getsize(path) > 49 * 1024 * 1024:
        await ctx.bot.send_message(
            chat_id,
            "❌ File hasil terlalu besar (>49 MB). Coba dengan file yang lebih kecil.",
            reply_markup=kb_back()
        )
        return

    with open(path, "rb") as f:
        await ctx.bot.send_document(
            chat_id=chat_id,
            document=f,
            filename=filename,
            caption=caption,
            reply_markup=kb_back()
        )


async def send_error(target, ctx: ContextTypes.DEFAULT_TYPE, error):
    """Kirim pesan error ke user."""
    chat_id = get_chat_id(target)
    await ctx.bot.send_message(
        chat_id,
        f"❌ Terjadi error:\n`{str(error)[:300]}`",
        parse_mode="Markdown",
        reply_markup=kb_back()
    )


def fmt_size(b: int) -> str:
    if b < 1024 * 1024:
        return f"{b/1024:.1f} KB"
    return f"{b/1024/1024:.2f} MB"


# ========== HANDLERS UTAMA ==========

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    cleanup(ctx)
    ctx.user_data.clear()
    await update.message.reply_text(
        "👋 *PDF Bot — Semua Fitur PDF!*\n\n"
        "🤖 Setara dengan ilovepdf.com, langsung di Telegram.\n\n"
        "Pilih fitur di bawah ini:",
        parse_mode="Markdown",
        reply_markup=kb_main()
    )
    return MENU


async def cb_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    cleanup(ctx)
    ctx.user_data.clear()
    await q.edit_message_text("📂 Pilih fitur PDF:", reply_markup=kb_main())
    return MENU


async def cb_feature(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    feat = q.data
    ctx.user_data["feat"]  = feat
    ctx.user_data["files"] = []

    text, next_state = FEATURE_INFO.get(feat, ("Kirim file PDF:", WAIT_FILE))
    await q.edit_message_text(text, parse_mode="Markdown", reply_markup=kb_back())
    return next_state


# ========== PENERIMA FILE ==========

async def recv_single_file(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    feat = ctx.user_data.get("feat")
    if not feat:
        await update.message.reply_text("Mulai dengan /start")
        return MENU

    result = await download_tg_file(update, ctx)
    if not result:
        await update.message.reply_text("❌ Format file tidak didukung.")
        return WAIT_FILE

    path, filename = result

    if feat == "split":
        await update.message.reply_text("⏳ Memproses...")
        await proc_split(update, ctx, path)

    elif feat == "compress":
        await update.message.reply_text("⏳ Mengompres file...")
        await proc_compress(update, ctx, path)

    elif feat == "rotate":
        ctx.user_data["rot_file"] = path
        await update.message.reply_text("🔄 Pilih derajat rotasi:", reply_markup=kb_rotate())
        return WAIT_ROT

    elif feat == "pdf_jpg":
        await update.message.reply_text("⏳ Mengkonversi halaman ke gambar...")
        await proc_pdf_to_jpg(update, ctx, path)

    elif feat == "pdf_word":
        await update.message.reply_text("⏳ Mengkonversi ke Word (mungkin agak lama)...")
        await proc_pdf_to_word(update, ctx, path)

    elif feat == "watermark":
        ctx.user_data["wm_file"] = path
        await update.message.reply_text(
            "💧 Ketik teks watermark yang ingin ditambahkan:\n"
            "_(contoh: RAHASIA, DRAFT, nama kamu, dll)_",
            parse_mode="Markdown"
        )
        return WAIT_WM

    elif feat == "protect":
        ctx.user_data["prot_file"] = path
        await update.message.reply_text("🔑 Masukkan password untuk PDF ini:")
        return WAIT_PASS

    elif feat == "unlock":
        ctx.user_data["unlk_file"] = path
        await update.message.reply_text(
            "🔑 Masukkan password PDF ini:\n"
            "_(Ketik `skip` jika tidak tahu passwordnya)_",
            parse_mode="Markdown"
        )
        return WAIT_PASS

    elif feat == "pagenum":
        await update.message.reply_text("⏳ Menambahkan nomor halaman...")
        await proc_page_numbers(update, ctx, path)

    elif feat == "ocr":
        await update.message.reply_text("⏳ Melakukan OCR (mungkin agak lama)...")
        await proc_ocr(update, ctx, path)

    return WAIT_FILE


async def recv_multi_file(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    feat = ctx.user_data.get("feat")

    result = await download_tg_file(update, ctx)
    if not result:
        await update.message.reply_text("❌ Format file tidak didukung.")
        return WAIT_MULTI

    path, filename = result
    ctx.user_data["files"].append({"path": path, "name": filename})
    n = len(ctx.user_data["files"])

    if feat == "merge":
        await update.message.reply_text(
            f"✅ File PDF ke-{n}: *{filename}*\n\nKirim file berikutnya atau klik *Gabungkan*.",
            parse_mode="Markdown",
            reply_markup=kb_merge()
        )
    elif feat == "jpg_pdf":
        await update.message.reply_text(
            f"✅ Gambar ke-{n}: *{filename}*\n\nKirim gambar berikutnya atau klik *Buat PDF*.",
            parse_mode="Markdown",
            reply_markup=kb_jpg_pdf()
        )

    return WAIT_MULTI


async def cb_do_merge(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    files = ctx.user_data.get("files", [])
    if len(files) < 2:
        await q.answer("⚠️ Minimal 2 file PDF untuk digabung!", show_alert=True)
        return WAIT_MULTI
    await q.edit_message_text(f"⏳ Menggabungkan {len(files)} file PDF...")
    await proc_merge(q, ctx, files)
    return MENU


async def cb_do_jpg_pdf(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    files = ctx.user_data.get("files", [])
    if not files:
        await q.answer("⚠️ Belum ada gambar!", show_alert=True)
        return WAIT_MULTI
    await q.edit_message_text(f"⏳ Membuat PDF dari {len(files)} gambar...")
    await proc_jpg_to_pdf(q, ctx, files)
    return MENU


async def cb_rotate_choice(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    deg  = int(q.data.split("_")[1])
    path = ctx.user_data.get("rot_file")
    await q.edit_message_text(f"⏳ Memutar PDF {deg}°...")
    await proc_rotate(q, ctx, path, deg)
    return MENU


async def recv_password(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    feat = ctx.user_data.get("feat")
    pwd  = update.message.text.strip()

    if feat == "protect":
        path = ctx.user_data.get("prot_file")
        await update.message.reply_text("⏳ Menambahkan password...")
        await proc_protect(update, ctx, path, pwd)

    elif feat == "unlock":
        path = ctx.user_data.get("unlk_file")
        p = None if pwd.lower() == "skip" else pwd
        await update.message.reply_text("⏳ Membuka password PDF...")
        await proc_unlock(update, ctx, path, p)

    return MENU


async def recv_watermark_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    wm_text = update.message.text.strip()
    path    = ctx.user_data.get("wm_file")
    await update.message.reply_text("⏳ Menambahkan watermark...")
    await proc_watermark(update, ctx, path, wm_text)
    return MENU


# ========== PROCESSORS ==========

async def proc_merge(target, ctx: ContextTypes.DEFAULT_TYPE, files: list):
    out = None
    try:
        writer      = PdfWriter()
        total_pages = 0
        for f in files:
            reader = PdfReader(f["path"])
            for page in reader.pages:
                writer.add_page(page)
            total_pages += len(reader.pages)

        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
            writer.write(tmp)
            out = tmp.name

        await send_result(
            target, ctx, out, "merged.pdf",
            f"✅ Merge selesai!\n📄 {len(files)} file → {total_pages} halaman"
        )
    except Exception as e:
        logger.error(f"Merge error: {e}")
        await send_error(target, ctx, e)
    finally:
        if out:
            try: os.unlink(out)
            except: pass
        for f in files:
            try: os.unlink(f["path"])
            except: pass
        ctx.user_data["files"] = []


async def proc_split(update: Update, ctx: ContextTypes.DEFAULT_TYPE, path: str):
    out_zip = None
    try:
        reader  = PdfReader(path)
        n_pages = len(reader.pages)

        with tempfile.NamedTemporaryFile(delete=False, suffix=".zip") as zf:
            out_zip = zf.name

        with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED) as zout:
            for i, page in enumerate(reader.pages, 1):
                writer = PdfWriter()
                writer.add_page(page)
                with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
                    writer.write(tmp)
                    tmp_path = tmp.name
                zout.write(tmp_path, f"halaman_{i:03d}.pdf")
                os.unlink(tmp_path)

        await send_result(
            update, ctx, out_zip, "split_pages.zip",
            f"✅ Split selesai!\n📄 {n_pages} halaman → {n_pages} file PDF dalam ZIP"
        )
    except Exception as e:
        logger.error(f"Split error: {e}")
        await send_error(update, ctx, e)
    finally:
        if out_zip:
            try: os.unlink(out_zip)
            except: pass
        try: os.unlink(path)
        except: pass


async def proc_compress(update: Update, ctx: ContextTypes.DEFAULT_TYPE, path: str):
    out = None
    try:
        ori_size = os.path.getsize(path)

        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
            out = tmp.name

        with pikepdf.open(path) as pdf:
            pdf.save(
                out,
                compress_streams=True,
                object_stream_mode=pikepdf.ObjectStreamMode.generate,
                linearize=True
            )

        new_size  = os.path.getsize(out)
        saved_pct = (1 - new_size / ori_size) * 100 if ori_size > 0 else 0

        await send_result(
            update, ctx, out, "compressed.pdf",
            f"✅ Compress selesai!\n"
            f"📦 Sebelum : {fmt_size(ori_size)}\n"
            f"📦 Sesudah : {fmt_size(new_size)}\n"
            f"💾 Hemat   : {saved_pct:.1f}%"
        )
    except Exception as e:
        logger.error(f"Compress error: {e}")
        await send_error(update, ctx, e)
    finally:
        if out:
            try: os.unlink(out)
            except: pass
        try: os.unlink(path)
        except: pass


async def proc_rotate(target, ctx: ContextTypes.DEFAULT_TYPE, path: str, degrees: int):
    out = None
    try:
        reader = PdfReader(path)
        writer = PdfWriter()
        for page in reader.pages:
            page.rotate(degrees)
            writer.add_page(page)

        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
            writer.write(tmp)
            out = tmp.name

        await send_result(
            target, ctx, out, "rotated.pdf",
            f"✅ Rotasi {degrees}° selesai!\n📄 {len(reader.pages)} halaman"
        )
    except Exception as e:
        logger.error(f"Rotate error: {e}")
        await send_error(target, ctx, e)
    finally:
        if out:
            try: os.unlink(out)
            except: pass
        try: os.unlink(path)
        except: pass


async def proc_pdf_to_jpg(update: Update, ctx: ContextTypes.DEFAULT_TYPE, path: str):
    out_zip = None
    try:
        images = convert_from_path(path, dpi=150)

        with tempfile.NamedTemporaryFile(delete=False, suffix=".zip") as zf:
            out_zip = zf.name

        with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED) as zout:
            for i, img in enumerate(images, 1):
                with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as tmp:
                    img.save(tmp.name, "JPEG", quality=85)
                    tmp_path = tmp.name
                zout.write(tmp_path, f"halaman_{i:03d}.jpg")
                os.unlink(tmp_path)

        await send_result(
            update, ctx, out_zip, "pdf_to_jpg.zip",
            f"✅ Konversi selesai!\n🖼️ {len(images)} gambar JPG dalam ZIP"
        )
    except Exception as e:
        logger.error(f"PDF→JPG error: {e}")
        await send_error(update, ctx, e)
    finally:
        if out_zip:
            try: os.unlink(out_zip)
            except: pass
        try: os.unlink(path)
        except: pass


async def proc_jpg_to_pdf(target, ctx: ContextTypes.DEFAULT_TYPE, files: list):
    out       = None
    img_paths = []
    try:
        for f in files:
            img = Image.open(f["path"])
            if img.mode in ("RGBA", "P", "LA"):
                img = img.convert("RGB")
            with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as tmp:
                img.save(tmp.name, "JPEG", quality=90)
                img_paths.append(tmp.name)

        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
            out = tmp.name

        with open(out, "wb") as f_out:
            f_out.write(img2pdf.convert(img_paths))

        await send_result(
            target, ctx, out, "images_to_pdf.pdf",
            f"✅ Konversi selesai!\n📄 {len(files)} gambar → 1 PDF"
        )
    except Exception as e:
        logger.error(f"JPG→PDF error: {e}")
        await send_error(target, ctx, e)
    finally:
        if out:
            try: os.unlink(out)
            except: pass
        for p in img_paths:
            try: os.unlink(p)
            except: pass
        for f in files:
            try: os.unlink(f["path"])
            except: pass
        ctx.user_data["files"] = []


async def proc_pdf_to_word(update: Update, ctx: ContextTypes.DEFAULT_TYPE, path: str):
    out = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".docx") as tmp:
            out = tmp.name

        cv = Converter(path)
        cv.convert(out)
        cv.close()

        await send_result(update, ctx, out, "converted.docx",
                          "✅ PDF berhasil dikonversi ke Word (.docx)!")
    except Exception as e:
        logger.error(f"PDF→Word error: {e}")
        await send_error(update, ctx, e)
    finally:
        if out:
            try: os.unlink(out)
            except: pass
        try: os.unlink(path)
        except: pass


async def proc_watermark(update: Update, ctx: ContextTypes.DEFAULT_TYPE,
                         path: str, wm_text: str):
    wm_path = None
    out     = None
    try:
        # Buat halaman watermark
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
            wm_path = tmp.name

        c = rl_canvas.Canvas(wm_path, pagesize=A4)
        w, h = A4
        c.saveState()
        c.setFont("Helvetica-Bold", 50)
        c.setFillColor(Color(0.5, 0.5, 0.5, alpha=0.25))
        c.translate(w / 2, h / 2)
        c.rotate(45)
        c.drawCentredString(0, 0, wm_text)
        c.restoreState()
        c.save()

        # Overlay ke tiap halaman
        reader   = PdfReader(path)
        wm_page  = PdfReader(wm_path).pages[0]
        writer   = PdfWriter()

        for page in reader.pages:
            page.merge_page(wm_page)
            writer.add_page(page)

        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
            writer.write(tmp)
            out = tmp.name

        await send_result(update, ctx, out, "watermarked.pdf",
                          f"✅ Watermark '{wm_text}' berhasil ditambahkan!")
    except Exception as e:
        logger.error(f"Watermark error: {e}")
        await send_error(update, ctx, e)
    finally:
        for p in filter(None, [wm_path, out]):
            try: os.unlink(p)
            except: pass
        try: os.unlink(path)
        except: pass


async def proc_protect(update: Update, ctx: ContextTypes.DEFAULT_TYPE,
                       path: str, password: str):
    out = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
            out = tmp.name

        with pikepdf.open(path) as pdf:
            pdf.save(out, encryption=pikepdf.Encryption(
                user=password, owner=password, R=4
            ))

        await send_result(update, ctx, out, "protected.pdf",
                          f"✅ PDF berhasil diproteksi!\n🔑 Password: {password}")
    except Exception as e:
        logger.error(f"Protect error: {e}")
        await send_error(update, ctx, e)
    finally:
        if out:
            try: os.unlink(out)
            except: pass
        try: os.unlink(path)
        except: pass


async def proc_unlock(update: Update, ctx: ContextTypes.DEFAULT_TYPE,
                      path: str, password):
    out = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
            out = tmp.name

        open_kwargs = {"password": password} if password else {}
        with pikepdf.open(path, **open_kwargs) as pdf:
            pdf.save(out)

        await send_result(update, ctx, out, "unlocked.pdf",
                          "✅ Password PDF berhasil dihapus!")
    except pikepdf.PasswordError:
        await update.message.reply_text(
            "❌ Password salah! Coba lagi atau /start untuk mulai ulang.",
            reply_markup=kb_back()
        )
    except Exception as e:
        logger.error(f"Unlock error: {e}")
        await send_error(update, ctx, e)
    finally:
        if out:
            try: os.unlink(out)
            except: pass
        try: os.unlink(path)
        except: pass


async def proc_page_numbers(update: Update, ctx: ContextTypes.DEFAULT_TYPE, path: str):
    out = None
    try:
        reader  = PdfReader(path)
        n_pages = len(reader.pages)
        writer  = PdfWriter()

        for i, page in enumerate(reader.pages, 1):
            pw = float(page.mediabox.width)
            ph = float(page.mediabox.height)

            with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
                num_path = tmp.name

            c = rl_canvas.Canvas(num_path, pagesize=(pw, ph))
            c.setFont("Helvetica", 11)
            c.setFillColor(Color(0, 0, 0, alpha=0.7))
            c.drawCentredString(pw / 2, 20, f"- {i} -")
            c.save()

            page.merge_page(PdfReader(num_path).pages[0])
            writer.add_page(page)
            os.unlink(num_path)

        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
            writer.write(tmp)
            out = tmp.name

        await send_result(update, ctx, out, "numbered.pdf",
                          f"✅ Nomor halaman ditambahkan!\n📄 {n_pages} halaman")
    except Exception as e:
        logger.error(f"PageNum error: {e}")
        await send_error(update, ctx, e)
    finally:
        if out:
            try: os.unlink(out)
            except: pass
        try: os.unlink(path)
        except: pass


async def proc_ocr(update: Update, ctx: ContextTypes.DEFAULT_TYPE, path: str):
    out = None
    try:
        images = convert_from_path(path, dpi=200)
        full_text = ""

        for i, img in enumerate(images, 1):
            await update.message.reply_text(f"🔍 Memproses halaman {i}/{len(images)}...")
            text = pytesseract.image_to_string(img, lang="ind+eng")
            full_text += f"\n\n===== HALAMAN {i} =====\n{text}"

        with tempfile.NamedTemporaryFile(delete=False, suffix=".txt",
                                          mode="w", encoding="utf-8") as tmp:
            tmp.write(full_text)
            out = tmp.name

        await send_result(
            update, ctx, out, "ocr_result.txt",
            f"✅ OCR selesai!\n📄 {len(images)} halaman diproses\n"
            f"📝 Hasil tersimpan dalam file .txt"
        )
    except Exception as e:
        logger.error(f"OCR error: {e}")
        await send_error(update, ctx, e)
    finally:
        if out:
            try: os.unlink(out)
            except: pass
        try: os.unlink(path)
        except: pass


# ========== FALLBACK ==========

async def unknown_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "❓ Ketik /start untuk memulai.",
        reply_markup=kb_back()
    )


# ========== MAIN ==========

def main():
    app = Application.builder().token(TOKEN).build()

    features        = list(FEATURE_INFO.keys())
    pattern_features = f"^({'|'.join(features)})$"

    conv = ConversationHandler(
        entry_points=[CommandHandler("start", cmd_start)],
        states={
            MENU: [
                CallbackQueryHandler(cb_feature, pattern=pattern_features),
            ],
            WAIT_FILE: [
                MessageHandler(filters.Document.ALL | filters.PHOTO, recv_single_file),
                CallbackQueryHandler(cb_menu, pattern="^menu$"),
            ],
            WAIT_MULTI: [
                MessageHandler(filters.Document.ALL | filters.PHOTO, recv_multi_file),
                CallbackQueryHandler(cb_do_merge,   pattern="^do_merge$"),
                CallbackQueryHandler(cb_do_jpg_pdf, pattern="^do_jpg_pdf$"),
                CallbackQueryHandler(cb_menu,       pattern="^menu$"),
            ],
            WAIT_ROT: [
                CallbackQueryHandler(cb_rotate_choice, pattern=r"^rot_(90|180|270)$"),
                CallbackQueryHandler(cb_menu,          pattern="^menu$"),
            ],
            WAIT_PASS: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, recv_password),
                CallbackQueryHandler(cb_menu, pattern="^menu$"),
            ],
            WAIT_WM: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, recv_watermark_text),
                CallbackQueryHandler(cb_menu, pattern="^menu$"),
            ],
        },
        fallbacks=[
            CommandHandler("start", cmd_start),
            CallbackQueryHandler(cb_menu, pattern="^menu$"),
        ],
        allow_reentry=True,
    )

    app.add_handler(conv)
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, unknown_message))

    logger.info("🚀 PDF Bot mulai berjalan...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
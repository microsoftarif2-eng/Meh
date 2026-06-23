# VoiceForge Studio
import os
import asyncio
import logging
import subprocess
import gradio as gr
import shutil

# ---------------------------------------------------------
# Windows event loop fix (prevents refresh spam)
# ---------------------------------------------------------
asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# ---------------------------------------------------------
# Logging
# ---------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S"
)
log = logging.getLogger("VoiceForge")

# ---------------------------------------------------------
# Import SQL helpers
# ---------------------------------------------------------
from tools.scripts.sqlHelper import (
    init_all,
    get_all_voice_types,
    get_all_tags,
    get_all_tags_no_never,
    get_voice_data,
    get_voice_data_manual,
    clear_temp_data,
    insert_temp_data,
    get_temp_ref_matches,
    clear_temp_gen_files,
    get_temp_gen_files,
    update_voice_tags,
    update_temp_data_tags,
    remove_tag_from_voice_type
)

# ---------------------------------------------------------
# Import matcher + extractor
# ---------------------------------------------------------
from tools.scripts.textMatcher import get_best_matches
from tools.BsaBrowser.bsaExtract import extract_audio
from tools.scripts.train_custom_voice import train_voice

# ---------------------------------------
# CLEANUP FUNCTION
# ---------------------------------------
def clear_extracted_wavs(voice_type, keep_files):
    root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__)))
    folder = os.path.join(root_dir, "data", "extractedwavs", voice_type.lower())

    print(f"\n[Cleanup] Target folder: {folder}")
    print(f"[Cleanup] Keeping {len(keep_files)} files")

    # If folder exists, delete everything inside it EXCEPT keep_files
    if os.path.isdir(folder):
        for fname in os.listdir(folder):
            full = os.path.join(folder, fname)
            if full not in keep_files:
                try:
                    os.remove(full)
                except:
                    pass
    else:
        os.makedirs(folder, exist_ok=True)

    # Ensure kept files exist in the folder
    for f in keep_files:
        if os.path.isfile(f):
            # Already in place — nothing to do
            pass

# Clear all folders/files in data/extractedwavs on startup
def clear_all_extracted_wavs():
    root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__)))
    base_folder = os.path.join(root_dir, "data", "extractedwavs")

    print(f"\n[Startup Cleanup] Wiping ALL extracted WAVs")
    print(f"[Startup Cleanup] Target folder: {base_folder}")

    # Delete entire extractedwavs folder
    if os.path.isdir(base_folder):
        shutil.rmtree(base_folder)

    # Recreate empty folder
    os.makedirs(base_folder, exist_ok=True)

    print(f"[Startup Cleanup] Completed. Folder reset.")

# ---------------------------------------
# WRITE TEXT LINE TO TEMP FILE
# ---------------------------------------
TEXT_LINES_PATH = os.path.join("tools", "scripts", "textLines.txt")

def write_text_line(text: str):
    print("\n--- WRITE_TEXT_LINE() ---")
    print("TEXT_LINES_PATH =", TEXT_LINES_PATH)
    print("Writing line:", f"{text}")
    print("-------------------------\n")

    with open(TEXT_LINES_PATH, "w", encoding="utf-8") as f:
        f.write(f"{text}\n")

    return text

# ---------------------------------------------------------
# Initialize DB tables
# ---------------------------------------------------------
init_all()
clear_all_extracted_wavs()
# ---------------------------------------------------------
# Load voice types
# ---------------------------------------------------------
VOICE_TYPES = [vt for vt, _ in get_all_voice_types()]
ALL_TAGS = get_all_tags()
ALL_TAGS_NO_NEVER = get_all_tags_no_never()
# ---------------------------------------------------------
# Placeholder TTS backend
# ---------------------------------------------------------
def generate_tts_backend(voice_type: str, text: str):
    print("\n=== GENERATE_TTS_BACKEND ===")
    print("voice_type =", voice_type)
    print("text =", text)

    if not text.strip():
        return [], "No text provided."

    # Write raw multiline content directly
    with open(TEXT_LINES_PATH, "w", encoding="utf-8") as f:
        f.write(text.strip() + "\n")

    # Run generator
    train_voice(voice_type)

    # Parse lines from input
    lines = []
    for raw_line in text.strip().splitlines():
        if "|" not in raw_line:
            continue
        name, content = raw_line.split("|", 1)
        lines.append(name.strip())

    root_output = os.path.join("generated", voice_type.lower())

    results = []

    for line_name in lines:
        line_dir = os.path.join(root_output, line_name)

        if not os.path.isdir(line_dir):
            continue

        for ref_folder in sorted(os.listdir(line_dir)):
            ref_dir = os.path.join(line_dir, ref_folder)

            wav_path = os.path.join(ref_dir, f"{line_name}.wav")

            if os.path.isfile(wav_path):
                results.append({
                    "line": line_name,
                    "ref": ref_folder,
                    "path": os.path.abspath(wav_path)
                })

    return results, f"Generated {len(results)} files."

# ---------------------------------------------------------
# Gradio wrapper for TTS
# ---------------------------------------------------------
def ui_generate(voice_type, text):
    if not text.strip():
        return [gr.update(value=None, visible=False) for _ in range(50)] + ["Please enter text."]

    # Write text file
    with open(TEXT_LINES_PATH, "w", encoding="utf-8") as f:
        f.write(text.strip() + "\n")

    # Run generation
    train_voice(voice_type)

    # Parse input lines
    lines = []
    for raw_line in text.strip().splitlines():
        if "|" not in raw_line:
            continue
        name, _ = raw_line.split("|", 1)
        lines.append(name.strip())

    root_output = os.path.join("generated", voice_type.lower())

    wavs = []

    for line_name in lines:
        line_dir = os.path.join(root_output, line_name)

        if not os.path.isdir(line_dir):
            continue

        for ref_folder in sorted(os.listdir(line_dir)):
            ref_dir = os.path.join(line_dir, ref_folder)
            wav_path = os.path.join(ref_dir, f"{line_name}.wav")

            if os.path.isfile(wav_path):
                wavs.append(os.path.abspath(wav_path))
            else:
                wavs.append(None)

    audio_updates = []

    for i in range(50):
        if i < len(wavs) and wavs[i] is not None:
            audio_updates.append(gr.update(value=wavs[i], visible=True))
        else:
            audio_updates.append(gr.update(value=None, visible=False))

    # ---------------------------------------------------
    # Save generated files to syTempData
    # stData: generated\<voicetype>\<lineName>\<#>\<fileName>^voiceText
    # ---------------------------------------------------
    try:
        clear_temp_gen_files()
        # Parse text lines to get voice text per line name
        voice_text_map = {}
        for raw_line in text.strip().splitlines():
            if "|" not in raw_line:
                continue
            name, line_text = raw_line.split("|", 1)
            voice_text_map[name.strip()] = line_text.strip()

        for line_name in lines:
            line_dir = os.path.join(root_output, line_name)
            if not os.path.isdir(line_dir):
                continue
            voice_text = voice_text_map.get(line_name, "")
            for ref_folder in sorted(os.listdir(line_dir)):
                ref_dir = os.path.join(line_dir, ref_folder)
                wav_path = os.path.join(ref_dir, f"{line_name}.wav")
                if os.path.isfile(wav_path):
                    # Format: generated\<voicetype>\<lineName>\<#>\<fileName>^voiceText
                    rel_path = os.path.join("generated", voice_type.lower(), line_name, ref_folder, f"{line_name}.wav")
                    data_str = f"{rel_path}^{voice_text}"
                    insert_temp_data("genFile", data_str)

        log.info(f"[syTempData] Saved genFile rows")
    except Exception as e:
        log.error(f"[syTempData] Failed to save genFile rows: {e}")

    return audio_updates + [
        f"Generation complete. {len([w for w in wavs if w])} files created."
    ]

# ---------------------------------------------------------
# Reference Finder
# ---------------------------------------------------------
def ui_find_refs(voice_type, text, ref_text, search_mode, count, include_tags, exclude_tags, min_length, *audio_values):
    ref_text = ref_text.strip() if ref_text else ""
    text     = text.strip()     if text     else ""

    keep_files = []
    for audio_path in audio_values:
        if audio_path:
            keep_files.append(audio_path)
            txt_path = os.path.splitext(audio_path)[0] + ".txt"
            if os.path.isfile(txt_path):
                keep_files.append(txt_path)

    clear_extracted_wavs(voice_type, keep_files)
    log.info(f"ui_find_refs: voice={voice_type}, mode={search_mode}, count={count}")

    empty = [gr.update(value=None, visible=False) for _ in range(10)]

    # --------------------------------------------------
    # MANUAL — SQL LIKE on vdVoiceText, no semantic/regex
    # --------------------------------------------------
    if search_mode == "Manual":
        if not ref_text:
            log.info("Manual mode requires Reference Search Text")
            return empty
        try:
            rows = get_voice_data_manual(
                voice_type, ref_text,
                include_tags=include_tags,
                exclude_tags=exclude_tags,
                limit=count,
                min_length=int(min_length or 0)
            )
            log.info(f"Manual search returned {len(rows)} rows")
        except Exception as e:
            log.error(f"Manual search error: {e}")
            return empty
        # rows are already (vdKey, filename, voiceText, vdTags) — use directly as matches
        matches = [(r[0], r[1], r[2], r[3] or "") for r in rows]

    # --------------------------------------------------
    # AUTO — semantic/regex on Dialogue Line
    # HYBRID — semantic/regex on Reference Search Text
    # --------------------------------------------------
    else:
        search_text = ref_text if (search_mode == "Hybrid" and ref_text) else text
        if not search_text:
            log.info("No search text available")
            return empty

        try:
            voice_rows = get_voice_data(voice_type, include_tags=include_tags, exclude_tags=exclude_tags, min_length=int(min_length or 0))
            log.info(f"Loaded {len(voice_rows)} voice rows (include={include_tags}, exclude={exclude_tags})")
        except Exception as e:
            log.error(f"get_voice_data error: {e}")
            return empty

        tags_lookup = {r[0]: (r[3] or "") for r in voice_rows}

        try:
            raw_matches = get_best_matches(search_text, voice_rows, top_k=count)
            log.info(f"Matcher returned {len(raw_matches)} results")
            matches = [(k, f, t, tags_lookup.get(k, "")) for k, f, t in raw_matches]
        except Exception as e:
            log.error(f"Matcher error: {e}")
            return empty

    # --------------------------------------------------
    # Save to syTempData
    # --------------------------------------------------
    try:
        clear_temp_data()
        for ui_index, (vd_key, filename, voice_text, vd_tags) in enumerate(matches):
            data_str = f"{ui_index}~{vd_key}~{voice_type}~{filename}~{voice_text}~{vd_tags}"
            insert_temp_data("refMatch", data_str)
        log.info(f"[syTempData] Saved {len(matches)} refMatch rows")
    except Exception as e:
        log.error(f"[syTempData] Failed: {e}")

    # --------------------------------------------------
    # Extract WAVs
    # --------------------------------------------------
    wavs = []
    try:
        for vd_key, fuz_file, transcript, _tags in matches:
            log.info(f"Matched FUZ: {fuz_file} (vdKey={vd_key})")
            try:
                extract_audio(voice_type, fuz_file)
            except Exception as e:
                log.error(f"Extraction error for {fuz_file}: {e}")

            base_name = os.path.splitext(fuz_file)[0]
            wav_path = os.path.abspath(os.path.join("data", "extractedwavs", voice_type.lower(), base_name + ".wav"))
            txt_path = os.path.abspath(os.path.join("data", "extractedwavs", voice_type.lower(), base_name + ".txt"))

            try:
                with open(txt_path, "w", encoding="utf-8") as f:
                    f.write(transcript.strip())
            except Exception as e:
                log.error(f"Failed to save transcript for {fuz_file}: {e}")

            if os.path.isfile(wav_path):
                wavs.append(wav_path)
            else:
                log.warning(f"WAV missing: {wav_path}")
                wavs.append(None)

    except Exception as e:
        log.error(f"Unexpected error in wav loop: {e}")

    updates = []
    for i in range(10):
        if i < len(wavs) and wavs[i] is not None:
            updates.append(gr.update(value=wavs[i], visible=True))
        else:
            updates.append(gr.update(value=None, visible=False))

    return updates

# ---------------------------------------------------------
# Build ref match dropdown choices from syTempData
# Called after find_refs completes
# ---------------------------------------------------------
def ui_refresh_ref_dropdown():
    rows = get_temp_ref_matches()
    choices = []
    for stKey, stData in rows:
        parts = stData.split("~")
        # parts: uiIndex ~ vdKey ~ voicetype ~ filename ~ voiceText ~ tags...
        ui_index = parts[0] if len(parts) > 0 else "?"
        voice_text = parts[4] if len(parts) > 4 else ""
        preview = (voice_text[:12] + "...") if len(voice_text) > 12 else voice_text
        label = f"{int(ui_index) + 1}. {preview}"
        choices.append(label)
    return gr.update(choices=choices, value=None)

# ---------------------------------------------------------
# Load tags for a selected ref match into tag_for_line
# ---------------------------------------------------------
def ui_select_ref_match(selected_label):
    if not selected_label:
        return gr.update(value=[])

    rows = get_temp_ref_matches()
    choices = []
    for stKey, stData in rows:
        parts = stData.split("~")
        ui_index = parts[0] if len(parts) > 0 else "?"
        voice_text = parts[4] if len(parts) > 4 else ""
        preview = (voice_text[:12] + "...") if len(voice_text) > 12 else voice_text
        label = f"{int(ui_index) + 1}. {preview}"
        choices.append((label, stData))

    # Find the matching row
    stData = None
    for label, data in choices:
        if label == selected_label:
            stData = data
            break

    if not stData:
        return gr.update(value=[])

    parts = stData.split("~")
    # Tags are everything after the 5th piece, split individually
    # e.g. "...~^happy^normal^good^" → split on "^", filter empty
    raw_tags = parts[5] if len(parts) > 5 else ""
    tags = [t.strip() for t in raw_tags.split("^") if t.strip()]

    return gr.update(value=tags)

# ---------------------------------------------------------
# Save updated tags back to VoiceData via vdKey
# ---------------------------------------------------------
def ui_save_tags(selected_label, tags):
    if not selected_label:
        return gr.update(value="No ref match selected.")

    rows = get_temp_ref_matches()
    stKey_found = None
    stData = None
    for stKey, data in rows:
        parts = data.split("~")
        ui_index = parts[0] if len(parts) > 0 else "?"
        voice_text = parts[4] if len(parts) > 4 else ""
        preview = (voice_text[:12] + "...") if len(voice_text) > 12 else voice_text
        label = f"{int(ui_index) + 1}. {preview}"
        if label == selected_label:
            stKey_found = stKey
            stData = data
            break

    if not stData:
        return gr.update(value="Could not find selected ref match.")

    parts = stData.split("~")
    vd_key = int(parts[1]) if len(parts) > 1 else None

    if vd_key is None:
        return gr.update(value="Invalid vdKey.")

    try:
        update_voice_tags(vd_key, tags or [])
        update_temp_data_tags(stKey_found, tags or [])
        log.info(f"[Save Tags] vdKey={vd_key} stKey={stKey_found} → {tags}")
        return gr.update(value=f"Tags saved for key {vd_key}.")
    except Exception as e:
        log.error(f"[Save Tags] Failed: {e}")
        return gr.update(value=f"Error saving tags: {e}")

# ---------------------------------------------------------
# Build generated file dropdown choices from syTempData
# ---------------------------------------------------------
def ui_refresh_gen_dropdown():
    rows = get_temp_gen_files()
    choices = []
    for stKey, stData in rows:
        # stData: generated\<voicetype>\<lineName>\<#>\<fileName>^voiceText
        parts = stData.split("^", 1)
        rel_path = parts[0]
        path_parts = rel_path.replace("\\", "/").split("/")
        # path_parts: generated / voicetype / lineName / # / fileName
        line_name = path_parts[2] if len(path_parts) > 2 else "?"
        ref_num   = path_parts[3] if len(path_parts) > 3 else "?"
        label = f"{line_name} (ref #{ref_num})"
        choices.append(label)
    return gr.update(choices=choices, value=None)

# ---------------------------------------------------------
# LIP + FUZ a selected generated file, move .fuz to dest
# ---------------------------------------------------------
def ui_lip_fuz(selected_labels, dest_folder):
    if not selected_labels:
        return gr.update(value="No files selected.")
    if not dest_folder or not dest_folder.strip():
        return gr.update(value="No destination folder specified.")

    dest_folder = dest_folder.strip()

    root         = os.path.dirname(os.path.abspath(__file__))
    lip_gen_path = os.path.join(root, "tools", "fuzer", "LipGenerator.exe")
    lip_fuz_path = os.path.join(root, "tools", "fuzer", "LIPFuzer.exe")

    if not os.path.isfile(lip_gen_path):
        return gr.update(value=f"LipGenerator.exe not found: {lip_gen_path}")
    if not os.path.isfile(lip_fuz_path):
        return gr.update(value=f"LIPFuzer.exe not found: {lip_fuz_path}")

    # Build label → stData lookup
    rows = get_temp_gen_files()
    label_map = {}
    for stKey, data in rows:
        parts = data.split("^", 1)
        rel_path = parts[0]
        path_parts = rel_path.replace("\\", "/").split("/")
        line_name = path_parts[2] if len(path_parts) > 2 else "?"
        ref_num   = path_parts[3] if len(path_parts) > 3 else "?"
        label = f"{line_name} (ref #{ref_num})"
        label_map[label] = data

    os.makedirs(dest_folder, exist_ok=True)

    status_lines = []

    for selected_label in selected_labels:
        stData = label_map.get(selected_label)
        if not stData:
            status_lines.append(f"✗ {selected_label} — not found in temp data")
            continue

        parts      = stData.split("^", 1)
        rel_path   = parts[0]
        voice_text = parts[1] if len(parts) > 1 else ""

        wav_path  = os.path.join(root, rel_path)
        work_dir  = os.path.dirname(wav_path)
        file_stem = os.path.splitext(os.path.basename(wav_path))[0]

        if not os.path.isfile(wav_path):
            status_lines.append(f"✗ {selected_label} — WAV not found")
            continue

        # Step 1 — Generate .lip
        try:
            subprocess.run([
                lip_gen_path, wav_path, voice_text,
                "-Language:USEnglish", "-GestureExaggeration:1.0"
            ], check=True)
        except subprocess.CalledProcessError as e:
            status_lines.append(f"✗ {selected_label} — LipGen failed: {e}")
            continue

        lip_path = os.path.join(work_dir, f"{file_stem}.lip")
        if not os.path.isfile(lip_path):
            status_lines.append(f"✗ {selected_label} — LIP not created")
            continue

        # Step 2 — Fuz
        try:
            subprocess.run([
                lip_fuz_path,
                "-s", work_dir, "-d", work_dir,
                "-a", "wav", "-l", "lip",
                "--norec", "-v", "1"
            ], check=True)
        except subprocess.CalledProcessError as e:
            status_lines.append(f"✗ {selected_label} — LIPFuzer failed: {e}")
            continue

        fuz_path = os.path.join(work_dir, f"{file_stem}.fuz")
        if not os.path.isfile(fuz_path):
            status_lines.append(f"✗ {selected_label} — FUZ not created")
            continue

        # Step 3 — Move .fuz flat to dest
        try:
            dest_fuz = os.path.join(dest_folder, f"{file_stem}.fuz")
            shutil.move(fuz_path, dest_fuz)
        except Exception as e:
            status_lines.append(f"✗ {selected_label} — move failed: {e}")
            continue

        # Step 4 — Cleanup
        for cleanup_path in [wav_path, lip_path]:
            try:
                os.remove(cleanup_path)
            except Exception:
                pass

        status_lines.append(f"✓ {selected_label}")
        log.info(f"[LIPFuz] Done: {dest_fuz}")

    summary = f"{sum(1 for l in status_lines if l.startswith('✓'))}/{len(selected_labels)} completed\n" + "\n".join(status_lines)
    return gr.update(value=summary)

# ---------------------------------------------------------
# Remove a tag from all VoiceData records for this voice type
# ---------------------------------------------------------
def ui_remove_tag(voice_type, tag):
    if not tag:
        return gr.update(value="No tag selected.")
    if not voice_type:
        return gr.update(value="No voice type selected.")
    try:
        count = remove_tag_from_voice_type(voice_type, tag)
        log.info(f"[Remove Tag] '{tag}' removed from {count} records for {voice_type}")
        return gr.update(value=f"Removed '{tag}' from {count} records.")
    except Exception as e:
        log.error(f"[Remove Tag] Failed: {e}")
        return gr.update(value=f"Error: {e}")

# ---------------------------------------------------------
# Archive generated files for a voice type
# Moves generated/<voicetype>/... → data/archive/<voicetype>
# ---------------------------------------------------------
def ui_archive_generated(voice_type):
    if not voice_type:
        return gr.update(value="No voice type selected.")

    root = os.path.dirname(os.path.abspath(__file__))
    src  = os.path.join(root, "generated", voice_type.lower())
    dst  = os.path.join(root, "data", "archive", voice_type.lower())

    if not os.path.isdir(src):
        return gr.update(value="Nothing to archive.")

    try:
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        # If destination already exists, merge into it
        if os.path.isdir(dst):
            for item in os.listdir(src):
                s = os.path.join(src, item)
                d = os.path.join(dst, item)
                if os.path.isdir(s):
                    shutil.copytree(s, d, dirs_exist_ok=True)
                else:
                    shutil.copy2(s, d)
            shutil.rmtree(src)
        else:
            shutil.move(src, dst)
        log.info(f"[Archive] Moved {src} → {dst}")
        return gr.update(value=f"Archived to data/archive/{voice_type.lower()}")
    except Exception as e:
        log.error(f"[Archive] Failed: {e}")
        return gr.update(value=f"Error: {e}")

# ---------------------------------------------------------
# Clear references handler
def ui_clear_refs():
    folder = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "extractedwavs")
    if os.path.isdir(folder):
        shutil.rmtree(folder)
    os.makedirs(folder, exist_ok=True)
    log.info(f"[Clear Refs] Wiped: {folder}")
    return [gr.update(value=None, visible=False) for _ in range(10)]

# ---------------------------------------------------------
# Clear generated handler
def ui_clear_generated():
    folder = os.path.join(os.path.dirname(os.path.abspath(__file__)), "generated")
    if os.path.isdir(folder):
        shutil.rmtree(folder)
    os.makedirs(folder, exist_ok=True)
    log.info(f"[Clear Generated] Wiped: {folder}")
    return [gr.update(value=None, visible=False) for _ in range(50)]

# ---------------------------------------------------------
# Build UI
# ---------------------------------------------------------
with gr.Blocks(title="VoiceForge Studio") as demo:
    gr.Markdown("# 🎙️ VoiceForge Studio")
    gr.Markdown("Generate Skyrim-quality voice lines and preview reference WAVs.")

    with gr.Row():

        # =====================================================
        # LEFT COLUMN — Inputs + Reference Tools
        # =====================================================
        with gr.Column(scale=1):

            # ============================
            # REMOVE TAG GROUP
            # ============================
            with gr.Group():
                gr.Markdown("### Remove Tag from Voice Type")
                with gr.Row():
                    remove_tag_dropdown = gr.Dropdown(
                        choices=ALL_TAGS_NO_NEVER,
                        label="Tag to Remove",
                        value=None,
                        scale=2
                    )
                    remove_tag_btn = gr.Button("Remove Tag", scale=1)
                remove_tag_status = gr.Textbox(
                    label=None,
                    interactive=False,
                    show_label=False,
                    placeholder="",
                )

            # ============================
            # SEARCH CONTROLS GROUP
            # ============================
            with gr.Group():
                gr.Markdown("### Tags & Search")

                with gr.Row():
                    include_tags = gr.Dropdown(
                        choices=ALL_TAGS,
                        multiselect=True,
                        label="Include Tags",
                        value=[],
                        scale=1
                    )
                    exclude_tags = gr.Dropdown(
                        choices=ALL_TAGS,
                        multiselect=True,
                        label="Exclude Tags",
                        value=[],
                        scale=1
                    )

                ref_text = gr.Textbox(
                    label="Reference Search Text",
                    placeholder='Search text — supports * wildcard (e.g. "you have*brother")',
                    lines=1,
                    max_lines=2
                )

                search_mode = gr.Radio(
                    choices=["Auto", "Hybrid", "Manual"],
                    value="Auto",
                    label="Search Mode",
                    info="Auto: semantic on Dialogue Line  |  Hybrid: semantic on Search Text  |  Manual: SQL LIKE on Search Text"
                )

                min_length = gr.Number(
                    label="Min Text Length",
                    value=0,
                    minimum=0,
                    step=1,
                    info="Only return voice lines with text length ≥ this value (0 = no filter)"
                )

            ref_match_dropdown = gr.Dropdown(
                choices=[],
                label="Ref Match",
                value=None,
                interactive=True
            )

            tag_for_line = gr.Dropdown(
                choices=ALL_TAGS,
                multiselect=True,
                label="Tags for...",
                value=[]
            )

            with gr.Row():
                update_tags_btn = gr.Button("Update Tags", scale=1)
                update_tags_status = gr.Textbox(
                    label=None,
                    interactive=False,
                    show_label=False,
                    placeholder="",
                    scale=2
                )

            gr.Markdown("### Reference Finder")
            ref_count = gr.Slider(
                1, 10,
                value=3,
                step=1,
                label="Number of Matches"
            )

            find_refs_btn = gr.Button("Find Reference WAVs")

            with gr.Row():
                clear_refs_btn = gr.Button("Clear", scale=1)
                gr.Markdown("<h2>Reference Matches</h2>")

            ref_outputs = []

            for i in range(10):
                with gr.Row():
                    audio = gr.Audio(
                        label=None,
                        type="filepath",
                        interactive=False,
                        visible=(i == 0),
                        scale=6
                    )
                    ref_outputs.append(audio)

        # =====================================================
        # RIGHT COLUMN — Status + Generate + Output
        # =====================================================
        with gr.Column(scale=1):
            voice_dropdown = gr.Dropdown(
                choices=VOICE_TYPES,
                label="Voice Type",
                value=VOICE_TYPES[0] if VOICE_TYPES else None
            )

            text_input = gr.Textbox(
                label="Dialogue Line",
                placeholder="Enter text to generate...",
                lines=4,
                max_lines=12
            )
            status_box = gr.Textbox(
                label="Status",
                interactive=False
            )

            generate_btn = gr.Button("Generate Voice Line")
            with gr.Row():
                clear_generated_btn = gr.Button("Clear", scale=1)
                archive_btn = gr.Button("Archive", scale=1)
                gr.Markdown("<h2>Generated Output</h2>")
            archive_status = gr.Textbox(
                label=None,
                interactive=False,
                show_label=False,
                placeholder="",
            )

            with gr.Group():
                gr.Markdown("### LIP & Fuz")
                gen_file_dropdown = gr.Dropdown(
                    choices=[],
                    label="Generated Files",
                    value=[],
                    multiselect=True,
                    interactive=True
                )
                dest_folder = gr.Textbox(
                    label="Destination Folder",
                    placeholder=r"e.g. C:\Skyrim\Data\Sound\Voice\MyMod.esp\MaleNord",
                    lines=1
                )
                with gr.Row():
                    lip_fuz_btn = gr.Button("LIP & Fuz", scale=1)
                    lip_fuz_status = gr.Textbox(
                        label=None,
                        interactive=False,
                        show_label=False,
                        placeholder="",
                        lines=4,
                        max_lines=10,
                        scale=3
                    )

            generated_outputs = []

            for i in range(50):
                with gr.Row():
                    audio = gr.Audio(
                        label=None,
                        type="filepath",
                        interactive=False,
                        visible=False,
                        scale=6
                    )
                    generated_outputs.append(audio)

    # Remove tag from all VoiceData for this voice type
    remove_tag_btn.click(
        fn=ui_remove_tag,
        inputs=[voice_dropdown, remove_tag_dropdown],
        outputs=[remove_tag_status]
    )

    # Archive generated output
    archive_btn.click(
        fn=ui_archive_generated,
        inputs=[voice_dropdown],
        outputs=[archive_status]
    )

    # Connect TTS
    generate_btn.click(
        fn=ui_generate,
        inputs=[voice_dropdown, text_input],
        outputs=generated_outputs + [status_box]
    ).then(
        fn=ui_refresh_gen_dropdown,
        inputs=[],
        outputs=[gen_file_dropdown]
    )

    # LIP & Fuz selected file
    lip_fuz_btn.click(
        fn=ui_lip_fuz,
        inputs=[gen_file_dropdown, dest_folder],
        outputs=[lip_fuz_status]
    )


    # Connect reference finder
    find_refs_btn.click(
        fn=ui_find_refs,
        inputs=[voice_dropdown, text_input, ref_text, search_mode, ref_count, include_tags, exclude_tags, min_length] + ref_outputs,
        outputs=ref_outputs
    ).then(
        fn=ui_refresh_ref_dropdown,
        inputs=[],
        outputs=[ref_match_dropdown]
    )

    # Load tags when a ref match is selected
    ref_match_dropdown.change(
        fn=ui_select_ref_match,
        inputs=[ref_match_dropdown],
        outputs=[tag_for_line]
    )

    # Save tags back to VoiceData
    update_tags_btn.click(
        fn=ui_save_tags,
        inputs=[ref_match_dropdown, tag_for_line],
        outputs=[update_tags_status]
    )

    # Connect clear refs
    clear_refs_btn.click(
        fn=ui_clear_refs,
        inputs=[],
        outputs=ref_outputs
    ).then(
        fn=lambda: (gr.update(choices=[], value=None), gr.update(value=[]), gr.update(value="")),
        inputs=[],
        outputs=[ref_match_dropdown, tag_for_line, update_tags_status]
    )

    # Connect clear generated
    clear_generated_btn.click(
        fn=ui_clear_generated,
        inputs=[],
        outputs=generated_outputs
    ).then(
        fn=lambda: gr.update(choices=[], value=[]),
        inputs=[],
        outputs=[gen_file_dropdown]
    )


if __name__ == "__main__":
    demo.launch(server_name="0.0.0.0", server_port=7861)
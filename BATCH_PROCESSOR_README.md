# Batch Video Processor

Complete video processing pipeline with CSV queue management for automated video editing, HLS/DASH conversion, and server upload.

## Features

- ✅ **Single file or CSV batch processing**
- ✅ **UUID generation and tracking**
- ✅ **Status tracking in CSV** (pending, editing, converting, uploading, completed, error)
- ✅ **Auto-confirm mode** (`-y` flag) for non-interactive processing
- ✅ **Preset validation and confirmation**
- ✅ **Complete pipeline**: edit → convert → upload
- ✅ **Error handling** with status logging
- ✅ **Resume support** (skips completed tasks)

## Installation

Ensure all dependencies are installed:

```bash
pip install -r requirements.txt
```

Project layout:
- `run.py` (this tool, entry point — stays at the repo root)
- `source/mkv_edit.py` (video editing)
- `source/mkv_to_m3u8_converter.py` (HLS/DASH conversion)
- `source/upload_server.py` (SSH server upload)
- `source/upload_r2.py` (Cloudflare R2 upload)
- `source/logger.py`, `source/hw_detector.py`, `source/packager_detector.py`, `source/config.py` (shared modules)

## Quick Start

### Single File Processing

Process a single video file interactively:

```bash
python batch_processor.py --input video.mkv
```

This will:
1. Validate the MKV file exists
2. Generate a UUID (you can confirm or provide custom)
3. Display preset configuration (profiles + edit_params)
4. Process through the pipeline: edit → convert → upload
5. Create a queue CSV file for tracking

### CSV Batch Processing

Create a CSV file with your video queue:

```csv
path,uuid,status,error
/path/to/video1.mkv,uuid-1,pending,
/path/to/video2.mkv,uuid-2,pending,
/path/to/video3.mkv,uuid-3,pending,
```

Then process the entire queue:

```bash
python batch_processor.py --csv queue.csv
```

### Auto-Confirm Mode (Non-Interactive)

Skip all confirmation prompts:

```bash
python batch_processor.py --csv queue.csv -y
```

Perfect for automated workflows, cron jobs, or CI/CD pipelines.

## Usage

```bash
python batch_processor.py [OPTIONS]
```

### Required Arguments (choose one)

- `--input, -i PATH` - Single MKV file to process
- `--csv, -c PATH` - CSV file with processing queue

### Optional Arguments

- `--presets, -p PATH` - Preset configuration file (default: `presets.json`)
- `--yes, -y` - Auto-confirm all prompts (non-interactive mode)
- `--no-edit` - Skip video editing step (go straight to conversion)
- `--verbose, -v` - Enable verbose logging

## Processing Pipeline

### Step-by-Step Flow

1. **Input Validation**
   - Verify file exists and is MKV format
   - Throw error if invalid

2. **UUID Assignment**
   - Load UUID from CSV, OR
   - Generate new UUID and confirm with user
   - Custom UUID input supported

3. **Preset Confirmation**
   - Load `presets.json`
   - Display quality profiles
   - Display edit parameters (or defaults)
   - Confirm with user (unless `-y` flag)

4. **Video Editing** (optional, skip with `--no-edit`)
   - Run `mkv_edit.py` with edit_params
   - Apply color grading and filters
   - Use hardware acceleration
   - Save edited file to `./edited/{uuid}.mkv`
   - Update task status: `editing`

5. **HLS/DASH Conversion**
   - Run `mkv_to_m3u8_converter.py`
   - Generate adaptive streaming files
   - Create landscape and portrait versions
   - Output to `./output/{uuid}/`
   - Update task status: `converting`

6. **Server Upload**
   - Run `upload_server.py`
   - Upload video folders to remote server
   - Auto-backup if configured
   - Update task status: `uploading`

7. **Status Update**
   - Mark task as `completed` on success
   - Mark as `error` with message on failure
   - Save progress to CSV after each task

## CSV Format

### Required Headers

```csv
path,uuid,status,error
```

### Fields

- **path** (required): Absolute or relative path to MKV file
- **uuid** (required): Unique identifier for the video
- **status** (optional): Current processing status
  - `pending` - Not yet processed
  - `editing` - Currently editing
  - `converting` - Currently converting to HLS/DASH
  - `uploading` - Currently uploading to server
  - `completed` - Successfully processed
  - `error` - Failed (see error field)
- **error** (optional): Error message if status is `error`

### Example

```csv
path,uuid,status,error
/videos/video1.mkv,1632293a-0689-42c8-a9c5-3b1f17246fa8,completed,
/videos/video2.mkv,4e2f9201-6dd3-4d9f-b7cb-21139df43094,error,Conversion failed: exit code 1
/videos/video3.mkv,88d44db9-7ce3-4833-9984-d41831d0f6d3,pending,
```

## Examples

### Example 1: Interactive Single File

```bash
python batch_processor.py --input /videos/movie.mkv --presets presets.json
```

Output:
```
UUID Assignment:
  File: movie.mkv
  Generated UUID: abc123-def456

Use this UUID? [Y/n/custom]: Y

PRESET CONFIGURATION
Quality Profiles:
  1. landscape_1080p_500kbps: 1920x1080 @ 500kbps
  2. landscape_1080p_1mbps: 1920x1080 @ 1Mbps
  3. landscape_1080p_2mbps: 1920x1080 @ 2Mbps

Edit Parameters:
  -vf eq=gamma=0.90:contrast=0.08...

Proceed with these presets? [Y/n]: Y

[Processing...]
```

### Example 2: Batch Processing with Auto-Confirm

```bash
python batch_processor.py --csv queue.csv -y
```

Processes all tasks without any user interaction.

### Example 3: Skip Editing (Convert Only)

```bash
python batch_processor.py --csv queue.csv --no-edit -y
```

Skips the editing step and goes straight to HLS/DASH conversion.

### Example 4: Resume Failed Queue

If processing was interrupted, simply run again:

```bash
python batch_processor.py --csv queue.csv -y
```

The tool will:
- Skip tasks with status `completed`
- Retry tasks with status `error` or `pending`
- Resume from where it left off

## Logs

All processing logs are saved to:

```
./log/batch_YYYYMMDD_HHMMSS.log
```

Each individual tool also creates its own log:
- `./log/edit_YYYYMMDD_HHMMSS.log` (mkv_edit.py)
- `./log/converter_YYYYMMDD_HHMMSS.log` (mkv_to_m3u8_converter.py)
- `./log/upload_YYYYMMDD_HHMMSS.log` (upload_server.py)

## Error Handling

### Automatic Error Tracking

When a task fails:
1. Status set to `error`
2. Error message saved to CSV
3. Processing continues to next task
4. CSV updated after each task

### Manual Error Recovery

Edit the CSV to reset failed tasks:

```csv
path,uuid,status,error
/videos/video1.mkv,uuid-1,pending,
```

Change `status` from `error` to `pending` and clear `error` field.

## Integration with Other Tools

### With Cron Jobs

```bash
# Process queue every day at 2 AM
0 2 * * * cd /path/to/remuxer && python3 batch_processor.py --csv queue.csv -y >> cron.log 2>&1
```

### With CI/CD

```yaml
# GitHub Actions example
- name: Process video queue
  run: |
    python batch_processor.py --csv queue.csv -y
```

### With File Watchers

```bash
# Watch for new videos and add to queue
while true; do
  find /videos -name "*.mkv" -newer last_run >> new_videos.txt
  # Add to CSV...
  python batch_processor.py --csv queue.csv -y
  touch last_run
  sleep 3600
done
```

## Configuration

All processing configuration is in `presets.json`:

```json
{
  "profiles": [
    {
      "name": "landscape_1080p_500kbps",
      "resolution": "1920x1080",
      "bitrate": "500k"
    }
  ],
  "edit_params": [
    "-vf", "eq=gamma=0.90:contrast=0.08",
    "-c:v", "hevc_videotoolbox",
    "-b:v", "100M"
  ]
}
```

Server upload configuration is in `.env`:

```bash
SSH_HOST=your.server.com
SSH_PORT=22
SSH_USER=username
SSH_KEY_PATH=/path/to/ssh/key
SERVER_BASE_PATH=/var/www/videos
```

## Troubleshooting

### "File not found" error

Check that file paths in CSV are correct (absolute or relative to script directory).

### "Invalid file type" error

Only `.mkv` files are supported. Convert other formats to MKV first.

### Task stuck in "editing" status

Kill the process and change status back to `pending` in CSV, then retry.

### Upload fails

Check `.env` configuration and SSH connectivity:

```bash
ssh -i /path/to/key user@server.com
```

### No hardware acceleration

Install GPU drivers (NVIDIA) or use Apple Silicon/Intel Mac for VideoToolbox.

## Performance Tips

1. **Use `-y` flag** for unattended processing
2. **Enable hardware acceleration** (auto-detected)
3. **Use `--no-edit`** if no filters needed (faster)
4. **Process during off-hours** (less system load)
5. **Monitor disk space** (output files can be large)

## License

MIT License - Feel free to modify and distribute.

## Support

For issues or questions, check the log files first:
- `./log/batch_*.log` - Main processing log
- Individual tool logs in `./log/`

---

**Happy Processing! 🎬✨**

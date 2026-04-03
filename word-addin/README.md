# Lawttorney Word Add-in

AI-powered legal research, drafting, and review inside Microsoft Word.

## Features

| Tab | What It Does |
|-----|-------------|
| **Research** | Select text in Word or type a query -> AI finds relevant cases, legislation, analysis |
| **Draft** | Describe what you need -> AI generates legal document and inserts at cursor |
| **Review** | Select a clause -> AI flags risks, compliance issues, suggests improvements |
| **Cite** | Search for a case/statute -> AI finds and inserts formatted citation |

All tabs support:
- **Insert into Document** — places AI output at your cursor in Word
- **Export .docx** — downloads as a formatted Word file
- **Copy** — copies to clipboard

## How to Sideload (Test Locally)

### Option 1: Via Word Desktop (Windows)

1. Open Word
2. Go to **Insert** > **My Add-ins** > **Upload My Add-in**
3. Browse to `word-addin/manifest.xml`
4. Click **Upload**
5. The "Lawttorney AI" button appears in the Home tab

### Option 2: Via Word Online

1. Go to [Word Online](https://www.office.com/launch/word)
2. Open a document
3. Go to **Insert** > **Office Add-ins** > **Upload My Add-in**
4. Upload `manifest.xml`

### Option 3: Serve locally (for development)

Since the manifest points to `https://localhost:3000`, you need a local HTTPS server:

```bash
# Install a simple HTTPS server
npx http-server word-addin/ -p 3000 --ssl --cert cert.pem --key key.pem

# Or use Python
cd word-addin
python -m http.server 3000
```

Then update `manifest.xml` to use `http://localhost:3000` (for dev only).

## Configuration

After loading the add-in:
1. Click **Settings** in the sidebar
2. Enter your **API URL** (default: `https://tool.lawttorney.com/pyapiv2`)
3. Enter your **API Key**
4. Click **Test Connection** to verify

## Files

| File | Purpose |
|------|---------|
| `manifest.xml` | Office add-in manifest (registers with Word) |
| `taskpane.html` | Sidebar UI layout |
| `taskpane.css` | Styles (matches Lawttorney theme) |
| `taskpane.js` | API calls, Word interaction, markdown rendering |

## API Endpoints Used

- `GET /health` — Connection test
- `POST /search` — Research, drafting, review, citation queries
- `POST /export` — Generate .docx/.pdf from AI response

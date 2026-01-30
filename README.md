# Station Link 🔗

**Station Link** is a local agent designed to bridge the gap between modern Web Applications (SaaS) and local hardware. It runs silently in the background, exposing a secure localhost API that allows web browsers to perform privileged operations such as **Cryptographic Signing** and **Raw Hardware Printing**.

This project solves the "Browser Sandbox" limitation, enabling web-based ERPs, POS systems, and commercial applications to interact with the physical machine securely and directly.

## 🛡️ Core Features

### 1. Cryptographic Machine Identity
Station Link assigns a unique, immutable identity to the computer it runs on.
* **RSA 2048-bit Key Generation:** Automatically generates an asymmetric key pair upon first launch.
* **Secure Storage:**
    * **Software Mode (Default):** keys are stored locally with strict OS-level permissions (ACLs on Windows, `chmod 600` on Linux), preventing unauthorized access by other users.
    * **Hardware Mode (Ready):** Architecture prepared for TPM 2.0 integration.
* **Non-Repudiation:** Applications can request the agent to sign payloads (transactions, logs) using the private key. This proves that a specific physical machine authorized an action.

### 2. Direct Hardware Printing
Bypasses the standard browser print dialogs to communicate directly with thermal and laser printers.
* **RAW / ESC/POS Support:** Sends raw bytes directly to the printer spooler. Ideal for thermal printers (cutting paper, opening cash drawers, printing barcodes).
* **PDF/Image Support:** Accepts Base64 encoded files for printing via standard OS drivers (A4 reports, invoices).
* **Multi-Platform:** Uses `win32print` for Windows Spooler and is architected for CUPS on Linux.

---

## 🚀 Installation & Usage

### Windows (End User)
1.  Download the `StationLink.exe`.
2.  Place it in your Windows **Startup** folder to ensure it runs automatically.
3.  Run the application. A purple icon will appear in the System Tray (near the clock).
4.  **Configuration:** Right-click the tray icon and select **"Configure"** (or open `http://localhost:4321`) to select the default printer.

### Linux
1.  Download the binary or run from source.
2.  Ensure `libcups` and python dependencies are installed.
3.  Run `./StationLink`.

---

## 🔌 API Documentation

Station Link exposes a local HTTP server at `http://localhost:4321`. Your web application should make AJAX/Fetch requests to these endpoints.

### 1. Get Machine Identity
Retrieves the public key and hardware fingerprint to register the terminal in your backend.

* **Endpoint:** `GET /identity`
* **Response:**
```json
{
  "fingerprint": "a1b2c3d4...", 
  "public_key": "-----BEGIN PUBLIC KEY-----\nMIIBIjANBgkqhki...",
  "security_source": "SOFTWARE_FILE",
  "platform": "Windows"
}
```

### 2. Sign Data (Authentication)
Requests the agent to sign a payload using the machine's private key.

* **Endpoint:** `POST /sign`
* **Payload:**
```json
{ "payload": "transaction_id:998877|timestamp:1700000000" }
```
* **Response:**
```json
{
  "signature": "7f8e9d...", 
  "algorithm": "RSA-SHA256",
  "fingerprint": "a1b2c3d4..."
}
```

### 3. Print Job
Sends content to the configured printer.

* **Endpoint:** `POST /print`
* **Payload (RAW / Thermal):**
```json
{
  "type": "raw",
  "content": "MY STORE\nItem 1.....$10.00\n\n\n"
}
```
* **Payload (PDF / A4):**
```json
{
  "type": "file",
  "content": "<base64_string_of_pdf_file>"
}
```

---

## 🛠️ Development

### Prerequisites
* Python 3.8+
* **Windows:** `pywin32` libraries.
* **Linux:** `pycups` libraries.

### Building the Executable
This project uses **PyInstaller** to generate standalone executables. The build scripts automatically handle virtual environments and dependencies.

**Windows:**
Double-click `gerar_exe.bat`. The output will be in the `dist/` folder.

**Linux:**
Run the build script:
```bash
chmod +x gerar_bin.sh
./gerar_bin.sh
```

### Project Structure
* `station_link.py`: Main entry point, API logic, and Security Manager.
* `templates/`: HTML files for the local configuration interface.
* `requirements.txt`: Python dependencies (OS-specific markers included).

---

## 🔒 Security Note
This agent runs a local web server with **CORS enabled**. While it allows web pages to communicate with it, sensitive operations (like signing) rely on the fact that the private key **never leaves the local machine**. Ensure your web application validates the signatures against the registered public key on your backend.

## 📄 License
[MIT License](LICENSE)
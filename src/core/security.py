import hashlib
import platform
import uuid
from .config import KEY_FILE, SISTEMA, logger
import os

# --- CRIPTOGRAFIA ---
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import rsa, padding
from cryptography.hazmat.primitives import serialization

# Importações de SO (Windows)
if SISTEMA == "Windows":
    import win32api
    import win32security
    import ntsecuritycon as con

"""Gerenciamento da identidade da Station, chaves RSA e assinatura de payload."""

class StationSecurity:
    def __init__(self):
        self.private_key = None
        self.public_key_pem = None
        self.machine_fingerprint = self._get_machine_fingerprint()
        self.security_source = "UNKNOWN"
        self._load_or_generate_keys()

    def _get_machine_fingerprint(self):
        try:
            mac = uuid.getnode()
            info = f"{SISTEMA}-{platform.node()}-{mac}"
            return hashlib.sha256(info.encode()).hexdigest()
        except Exception:
            return "unknown"

    def _load_or_generate_keys(self):
        # Tenta carregar do arquivo local
        if os.path.exists(KEY_FILE):
            if self._load_from_file():
                self.security_source = "FILE"
        else:
            self._generate_new_keys()
            self.security_source = "FILE"

    def _generate_new_keys(self):
        self.private_key = rsa.generate_private_key(
            public_exponent=65537,
            key_size=2048,
        )
        self._save_to_file()
        self._load_public_key()

    def _save_to_file(self):
        pem = self.private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption()
        )
        with open(KEY_FILE, 'wb') as f:
            f.write(pem)
        self._secure_file_permissions(KEY_FILE)

    def _load_from_file(self):
        try:
            with open(KEY_FILE, "rb") as key_file:
                self.private_key = serialization.load_pem_private_key(
                    key_file.read(),
                    password=None,
                )
            self._load_public_key()
            return True
        except Exception as e:
            logger.error(f"Erro ao ler chave: {e}")
            return False

    def _load_public_key(self):
        public_key = self.private_key.public_key()
        self.public_key_pem = public_key.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo
        ).decode('utf-8')

    def _secure_file_permissions(self, filepath):
        try:
            if SISTEMA == "Windows":
                user, domain, type = win32security.LookupAccountName("", win32api.GetUserName())
                sd = win32security.GetFileSecurity(filepath, win32security.DACL_SECURITY_INFORMATION)
                dacl = win32security.ACL()
                dacl.AddAccessAllowedAce(win32security.ACL_REVISION, con.FILE_GENERIC_READ | con.FILE_GENERIC_WRITE, user)
                sd.SetSecurityDescriptorDacl(1, dacl, 0)
                win32security.SetFileSecurity(filepath, win32security.DACL_SECURITY_INFORMATION, sd)
            else:
                os.chmod(filepath, 0o600)
        except Exception as e:
            logger.warning(f"Erro permissões: {e}")

    def sign_payload(self, payload_string):
        data = payload_string.encode('utf-8')
        signature = self.private_key.sign(
            data,
            padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.MAX_LENGTH),
            hashes.SHA256()
        )
        return signature.hex()
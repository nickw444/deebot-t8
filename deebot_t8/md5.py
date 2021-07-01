from hashlib import md5


def md5_hex(text: str):
    return md5(bytes(str(text), "utf8")).hexdigest()

import urllib.request

from shop import settings

GATEWAY = "https://sms.example/send"


def send(number, text):
    if not settings.get_bool("sms", "enabled"):
        return False
    data = f"from={settings.get('sms', 'sender')}&to={number}&text={text}".encode()
    with urllib.request.urlopen(GATEWAY, data=data, timeout=5) as response:
        return response.status == 200

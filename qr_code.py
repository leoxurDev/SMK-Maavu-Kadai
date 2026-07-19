import qrcode

url = "https://smkmaavukadai.duckdns.org/"

qr = qrcode.QRCode(
    version=1,
    error_correction=qrcode.constants.ERROR_CORRECT_M,
    box_size=10,
    border=4,
)

qr.add_data(url)
qr.make(fit=True)

img = qr.make_image(fill_color="black", back_color="white")
img.save("smkmaavukadai_qr.png")

print("QR code saved as smkmaavukadai_qr.png")

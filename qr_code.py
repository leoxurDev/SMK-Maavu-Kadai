import qrcode

url = "https://maps.app.goo.gl/5YMcy55AGxmJ5U5HA"

qr = qrcode.QRCode(
    version=1,
    error_correction=qrcode.constants.ERROR_CORRECT_M,
    box_size=10,
    border=4,
)

qr.add_data(url)
qr.make(fit=True)

img = qr.make_image(fill_color="black", back_color="white")
img.save("location_for_the_shop.png")

print("QR code saved as location_for_the_shop.png")
    
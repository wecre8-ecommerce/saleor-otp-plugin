from setuptools import setup

setup(
    name="otp",
    version="0.1.0",
    packages=["otp"],
    package_dir={"otp": "otp"},
    description="OTP plugin",
    entry_points={
        "saleor.plugins": ["otp = otp.plugin:OTPPlugin"],
    },
)

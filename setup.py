from setuptools import setup, find_packages

setup(
    name="flask-github-issues",
    version="v0.1.3",
    packages=find_packages(),
    install_requires=["requests", "pytz"],
    author="Pontem Innovations",
    author_email="geoff@ponteminnovations.ca",
    description="A Python package for tracking Flask errors via GitHub Issues.",
    long_description=open("README.md").read(),
    long_description_content_type="text/markdown",
    url="https://github.com/Pontem-Innovations/flask-github-issues",
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
    ],
    python_requires=">=3.6",
)
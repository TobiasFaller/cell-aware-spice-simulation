# Installation

General instructions can be found here: [pyspice.fabrice-salvaire.fr](https://pyspice.fabrice-salvaire.fr/releases/latest/installation.html)

### Ubuntu / Debian

```bash
sudo apt-get install -y python3 python3-pip python3-scipy python3-regex libngspice0-dev
python3 -m pip install -r requirements.txt
pyspice-post-installation --check-install
```

### Windows

We strongly recommend using WSL and follow the Ubuntu guide instead.
The following steps are experimental:

1. Install anaconda or miniconda
2. Run the following commands in the Python environment's shell:

```console
python3 -m pip install -r requirements.txt
pyspice-post-installation --install-ngspice-dll
pyspice-post-installation --check-install
```

Ressources:

- https://pypi.org/project/PySpice/
- https://docs.conda.io/en/latest/miniconda.html
- http://ngspice.sourceforge.net/download.html

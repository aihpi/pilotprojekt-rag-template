FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PIP_NO_CACHE_DIR=1

WORKDIR /app

# Dependencies first → cached unless pyproject.toml changes
COPY pyproject.toml /app/
# torch defaults to the CUDA build, dragging in ~4.4 GB of nvidia-*/triton
# wheels that nothing here can use (Docker Desktop exposes no CUDA device, and
# docling's layout/table models run fine on CPU). Installing the CPU wheel first
# leaves torch already satisfied, so the resolve below never reaches for the GPU
# stack. On a CUDA host, drop this line to get GPU-accelerated docling.
#
# docling → rapidocr hard-requires opencv-python, the GUI build, which dlopens
# libxcb/libGL at `import cv2` and dies on slim images. Same cv2 module, no X11
# linkage → swap in the headless wheel rather than apt-installing a GUI stack
# this app never renders with. `pip check` flags rapidocr's now-unmet
# opencv-python pin; that is expected, cv2 still imports.
RUN pip install --upgrade pip \
    && pip install --index-url https://download.pytorch.org/whl/cpu torch torchvision \
    && pip install -e . \
    && pip uninstall -y opencv-python \
    && pip install opencv-python-headless

# App code + static assets last → fast rebuilds on CSS/JS changes
COPY . /app

EXPOSE 8000

CMD ["chainlit", "run", "app.py", "--host", "0.0.0.0", "--port", "8000"]

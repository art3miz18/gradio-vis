FROM python:3.9-slim

WORKDIR /app

# Copy and install requirements
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the Gradio interface
COPY gradio_interface.py .

EXPOSE 7860

CMD ["python", "gradio_interface.py"]
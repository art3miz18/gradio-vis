FROM python:3.9-slim

WORKDIR /app

# Install system dependencies if any (e.g., for specific libraries)
# RUN apt-get update && apt-get install -y --no-install-recommends some-package && rm -rf /var/lib/apt/lists/*

COPY ./requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY . /app

# Expose the port FastAPI will run on
EXPOSE 5000

# Command to run the FastAPI application
# Ensure main:app matches your FastAPI app instance in gateway/main.py
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "5001"]
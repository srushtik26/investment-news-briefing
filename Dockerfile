# Use an official, lightweight Python runtime as the base image
FROM python:3.11-slim

# Set the working directory inside the container
WORKDIR /app

# Copy only the requirements first to cache the dependency installation
COPY requirements.txt .

# Install dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of your application code into the container
COPY . .

# Expose Render's default port
EXPOSE 10000

# Start the FastAPI server using Uvicorn pointed to your web.py app object
# Render dynamically assigns the port via the $PORT environment variable
CMD ["sh", "-c", "uvicorn app.dashboard.web:app --host 0.0.0.0 --port ${PORT:-10000}"]
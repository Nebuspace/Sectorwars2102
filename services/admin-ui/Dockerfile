FROM node:18-alpine

WORKDIR /app

# Copy package files
COPY package.json package-lock.json ./

# Install dependencies (ci ensures reproducible builds from lock file)
RUN npm install --legacy-peer-deps --no-audit --no-fund

# Copy the rest of the application
COPY . .

# Expose port
EXPOSE 3001

# Start the application in development mode with hot reloading
CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0"]
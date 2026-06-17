FROM node:20-alpine

WORKDIR /app
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install
COPY frontend/ .
RUN npm run build

EXPOSE 3001
CMD ["npm", "run", "preview"]

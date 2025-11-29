FROM node:20-alpine
WORKDIR /app
COPY package*.json ./
RUN npm install
COPY . .
RUN npm run prisma:generate
EXPOSE 3001
CMD ["npm", "run", "start:dev"]

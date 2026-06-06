import { defineConfig } from "vitest/config";
import { resolve } from "path";

export default defineConfig({
  test: {
    // Env vars must be set before module imports since config.ts reads them at load time
    env: {
      SQLITE_PATH: ":memory:",
      JWT_SECRET: "test-secret-for-unit-tests",
      JWT_EXPIRY_SECONDS: "3600",
      MOBILE_JWT_EXPIRY_SECONDS: "604800",
      REFRESH_TOKEN_EXPIRY_SECONDS: "7776000",
      PAIRING_KEY: "test-pairing-key",
      CLOUD_RELAY_PORT: "8765",
      CLOUD_RELAY_HOST: "0.0.0.0",
      RELAY_PUBLIC_HOST: "localhost",
      MAX_OFFLINE_QUEUE: "500",
    },
    // Use forks to ensure each test file gets a fresh module state
    pool: "forks",
    poolOptions: {
      forks: {
        singleFork: false,
      },
    },
  },
});

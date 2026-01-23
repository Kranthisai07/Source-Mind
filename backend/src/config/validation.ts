import * as Joi from 'joi';

export const envValidationSchema = Joi.object({
  PORT: Joi.number().default(3001),
  DATABASE_URL: Joi.string().required(),
  DATABASE_DIRECT_URL: Joi.string().optional(),
  SHADOW_DATABASE_URL: Joi.string().optional(),
  OPENAI_API_KEY: Joi.string().allow('').optional(),
  JWT_SECRET: Joi.string().min(16).required(),
  FRONTEND_URL: Joi.string().optional(),
  MCP_CONFIG_PATH: Joi.string().optional(),
});

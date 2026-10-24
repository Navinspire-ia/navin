/**
 * Readable chat name for a Bedrock model ID (same rules as
 * navin/providers/model_names.py): `global.anthropic.claude-sonnet-5-5`
 * becomes "Claude Sonnet 5.5". Other IDs come back unchanged.
 */
const REGION_ID = /^(?:global|eu|us|us-gov|apac|au|jp|ca)\./i;
const VENDOR_ID =
  /^(?:anthropic|amazon|meta|openai|mistral|moonshot|moonshotai|zai|z-ai|qwen|deepseek|minimax|cohere|writer|google|nvidia|ai21|stability|twelvelabs|luma)\./i;
const WORD_CASE: Record<string, string> = { gpt: "GPT", glm: "GLM", oss: "OSS", ai: "AI", llm: "LLM", r1: "R1", v3: "V3" };

export function isBedrockModelId(modelId: string | null | undefined): boolean {
  const leaf = (modelId ?? "").trim().split("/").pop() ?? "";
  return REGION_ID.test(leaf) || VENDOR_ID.test(leaf);
}

export function modelDisplayName(modelId: string): string {
  const leaf = modelId.trim().split("/").pop() ?? modelId;
  if (!isBedrockModelId(leaf)) return leaf;
  const text = leaf
    .replace(REGION_ID, "")
    .replace(VENDOR_ID, "")
    .replace(/(?:-v?\d+)?:\d+$/, "")
    .replace(/-v\d+$/, "")
    .replace(/-\d{8}$/, "");
  const words: string[] = [];
  for (const part of text.split("-").filter(Boolean)) {
    const last = words[words.length - 1];
    if (/^\d+$/.test(part) && last && /^\d+(?:\.\d+)*$/.test(last)) {
      words[words.length - 1] = `${last}.${part}`;
      continue;
    }
    words.push(WORD_CASE[part.toLowerCase()] ?? (/^\d/.test(part) ? part : part[0].toUpperCase() + part.slice(1)));
  }
  return words.join(" ") || leaf;
}

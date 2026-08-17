/**
 * Minimal JSON Schema (draft-07 subset) validator.
 *
 * VERBATIM COPY of skills/product-technical-audit/src/validate.mjs, per
 * DECISIONS_V0.1 D-06: each skill stays independently distributable, so the
 * validator is duplicated rather than shared. The copy MUST NOT diverge in
 * behaviour; shared Evidence Card parity is enforced by
 * skills/_shared/tests/evidence-card-parity-test.mjs.
 *
 * Do not "improve" this file in isolation. Any change belongs in both copies,
 * or in a genuinely shared module extracted deliberately.
 *
 * Deliberately dependency-free: the repository has no validation library and
 * this skill must not add production dependencies at V0.1. Supported keywords
 * are exactly the ones used by the schemas in ../schema/:
 *   type, enum, const-by-enum, required, properties, additionalProperties:false,
 *   items, minItems, maxItems, minLength, minimum, maximum, pattern,
 *   $ref (local "#/definitions/x" and registry-resolved external ids).
 *
 * Anything else in a schema is ignored rather than silently passed as valid,
 * so extend this validator when a schema starts using a new keyword.
 */

const SUPPORTED = new Set([
  "$schema",
  "$id",
  "title",
  "description",
  "definitions",
  "type",
  "enum",
  "required",
  "properties",
  "additionalProperties",
  "items",
  "minItems",
  "maxItems",
  "minLength",
  "minimum",
  "maximum",
  "pattern",
  "$ref",
]);

function typeOf(value) {
  if (value === null) return "null";
  if (Array.isArray(value)) return "array";
  if (Number.isInteger(value)) return "integer";
  return typeof value;
}

function matchesType(value, expected) {
  const actual = typeOf(value);
  const list = Array.isArray(expected) ? expected : [expected];
  return list.some((candidate) => {
    if (candidate === "number") return actual === "number" || actual === "integer";
    if (candidate === "integer") return actual === "integer";
    return actual === candidate;
  });
}

function resolveRef(ref, root, registry) {
  if (ref.startsWith("#/")) {
    let node = root;
    for (const segment of ref.slice(2).split("/")) {
      node = node?.[segment];
    }
    if (!node) throw new Error(`unresolvable local $ref: ${ref}`);
    return { schema: node, root };
  }
  const external = registry?.[ref];
  if (!external) throw new Error(`unresolvable external $ref: ${ref}`);
  return { schema: external, root: external };
}

function walk(value, schema, path, root, registry, errors) {
  for (const keyword of Object.keys(schema)) {
    if (!SUPPORTED.has(keyword)) {
      errors.push({ path, message: `unsupported schema keyword "${keyword}"` });
    }
  }

  if (schema.$ref) {
    const resolved = resolveRef(schema.$ref, root, registry);
    walk(value, resolved.schema, path, resolved.root, registry, errors);
    return;
  }

  if (schema.type !== undefined && !matchesType(value, schema.type)) {
    errors.push({
      path,
      message: `expected type ${JSON.stringify(schema.type)}, got ${typeOf(value)}`,
    });
    return;
  }

  if (schema.enum !== undefined && !schema.enum.some((option) => option === value)) {
    errors.push({ path, message: `value not in enum ${JSON.stringify(schema.enum)}` });
  }

  const kind = typeOf(value);

  if (kind === "string") {
    if (schema.minLength !== undefined && value.length < schema.minLength) {
      errors.push({ path, message: `shorter than minLength ${schema.minLength}` });
    }
    if (schema.pattern !== undefined && !new RegExp(schema.pattern).test(value)) {
      errors.push({ path, message: `does not match pattern ${schema.pattern}` });
    }
  }

  if (kind === "number" || kind === "integer") {
    if (schema.minimum !== undefined && value < schema.minimum) {
      errors.push({ path, message: `below minimum ${schema.minimum}` });
    }
    if (schema.maximum !== undefined && value > schema.maximum) {
      errors.push({ path, message: `above maximum ${schema.maximum}` });
    }
  }

  if (kind === "array") {
    if (schema.minItems !== undefined && value.length < schema.minItems) {
      errors.push({ path, message: `fewer than minItems ${schema.minItems}` });
    }
    if (schema.maxItems !== undefined && value.length > schema.maxItems) {
      errors.push({ path, message: `more than maxItems ${schema.maxItems}` });
    }
    if (schema.items) {
      value.forEach((item, index) => {
        walk(item, schema.items, `${path}[${index}]`, root, registry, errors);
      });
    }
  }

  if (kind === "object") {
    for (const key of schema.required ?? []) {
      if (!Object.prototype.hasOwnProperty.call(value, key)) {
        errors.push({ path: `${path}.${key}`, message: "required property missing" });
      }
    }
    if (schema.additionalProperties === false && schema.properties) {
      for (const key of Object.keys(value)) {
        if (!Object.prototype.hasOwnProperty.call(schema.properties, key)) {
          errors.push({
            path: `${path}.${key}`,
            message: "additional property not allowed by contract",
          });
        }
      }
    }
    for (const [key, subSchema] of Object.entries(schema.properties ?? {})) {
      if (Object.prototype.hasOwnProperty.call(value, key)) {
        walk(value[key], subSchema, `${path}.${key}`, root, registry, errors);
      }
    }
  }
}

/**
 * @param {unknown} value
 * @param {object} schema
 * @param {Record<string, object>} [registry] external $id/filename -> schema
 * @returns {{ valid: boolean, errors: Array<{path: string, message: string}> }}
 */
export function validate(value, schema, registry = {}) {
  const errors = [];
  try {
    walk(value, schema, "$", schema, registry, errors);
  } catch (error) {
    errors.push({ path: "$", message: error.message });
  }
  return { valid: errors.length === 0, errors };
}

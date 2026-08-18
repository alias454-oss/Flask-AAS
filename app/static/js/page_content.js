// app/static/js/page_content.js
(() => {
  "use strict";

  const BLOCK_TAGS = new Set([
    "blockquote",
    "h1",
    "h2",
    "h3",
    "h4",
    "li",
    "ol",
    "p",
    "pre",
    "ul",
  ]);
  const VOID_TAGS = new Set(["br", "hr"]);

  const escapeText = (value) =>
    value
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;");

  const escapeAttribute = (value) =>
    escapeText(value).replaceAll('"', "&quot;");

  const openingTag = (element) => {
    const tag = element.tagName.toLowerCase();
    const attributes = Array.from(element.attributes)
      .map(
        (attribute) =>
          ` ${attribute.name}="${escapeAttribute(attribute.value)}"`
      )
      .join("");
    return `<${tag}${attributes}>`;
  };

  const serializeInline = (node, preserveWhitespace = false) => {
    if (node.nodeType === Node.TEXT_NODE) {
      const value = preserveWhitespace
        ? node.textContent
        : node.textContent.replace(/\s+/g, " ");
      return escapeText(value);
    }

    if (node.nodeType !== Node.ELEMENT_NODE) {
      return "";
    }

    const tag = node.tagName.toLowerCase();
    const start = openingTag(node);
    if (VOID_TAGS.has(tag)) {
      return start;
    }

    const content = Array.from(node.childNodes)
      .map((child) => serializeInline(child, preserveWhitespace || tag === "pre"))
      .join("");
    return `${start}${content}</${tag}>`;
  };

  const formatElement = (element, depth) => {
    const tag = element.tagName.toLowerCase();
    const indent = "  ".repeat(depth);
    const start = openingTag(element);

    if (VOID_TAGS.has(tag)) {
      return `${indent}${start}`;
    }

    if (tag === "pre") {
      const content = Array.from(element.childNodes)
        .map((child) => serializeInline(child, true))
        .join("");
      return `${indent}${start}${content}</${tag}>`;
    }

    const hasBlockChild = Array.from(element.children).some((child) =>
      BLOCK_TAGS.has(child.tagName.toLowerCase())
    );

    if (!hasBlockChild) {
      const content = Array.from(element.childNodes)
        .map((child) => serializeInline(child))
        .join("")
        .trim();
      return `${indent}${start}${content}</${tag}>`;
    }

    const lines = [`${indent}${start}`];
    let inlineBuffer = "";

    const flushInlineBuffer = () => {
      const content = inlineBuffer.replace(/\s+/g, " ").trim();
      if (content) {
        lines.push(`${"  ".repeat(depth + 1)}${content}`);
      }
      inlineBuffer = "";
    };

    Array.from(element.childNodes).forEach((child) => {
      if (
        child.nodeType === Node.ELEMENT_NODE &&
        BLOCK_TAGS.has(child.tagName.toLowerCase())
      ) {
        flushInlineBuffer();
        lines.push(formatElement(child, depth + 1));
        return;
      }
      inlineBuffer += serializeInline(child);
    });

    flushInlineBuffer();
    lines.push(`${indent}</${tag}>`);
    return lines.join("\n");
  };

  const formatPageContent = (content) =>
    Array.from(content.childNodes)
      .map((node) => {
        if (node.nodeType === Node.TEXT_NODE) {
          const text = node.textContent.replace(/\s+/g, " ").trim();
          return text ? escapeText(text) : "";
        }
        if (node.nodeType === Node.ELEMENT_NODE) {
          return formatElement(node, 0);
        }
        return "";
      })
      .filter(Boolean)
      .join("\n\n")
      .trim();

  document.addEventListener("DOMContentLoaded", async () => {
    const field = document.querySelector("[data-page-content-editor]");
    if (!field) {
      return;
    }

    const status = document.getElementById("page-content-load-status");
    const saveButton = document.querySelector("[data-page-content-save]");
    const pageKey = field.dataset.pageKey;
    const sourceUrl = field.dataset.sourceUrl;
    const customized = field.dataset.customized === "true";
    const hasErrors = field.dataset.hasErrors === "true";

    const setUnavailable = (message) => {
      field.disabled = true;
      if (saveButton) {
        saveButton.disabled = true;
      }
      if (status) {
        status.textContent = message;
      }
    };

    if (!pageKey || !sourceUrl) {
      setUnavailable("This page is not available for content editing.");
      return;
    }

    try {
      const response = await fetch(sourceUrl, {
        credentials: "same-origin",
        headers: { Accept: "text/html" },
      });

      if (!response.ok) {
        throw new Error(`Page request failed with status ${response.status}`);
      }

      const html = await response.text();
      const documentCopy = new DOMParser().parseFromString(html, "text/html");
      const content = documentCopy.querySelector(
        `[data-page-content="${pageKey}"]`
      );

      if (!content) {
        setUnavailable(
          "The current theme does not expose this page for content editing."
        );
        return;
      }

      // Preserve a rejected POST exactly as submitted so validation never
      // discards administrator input. Normal GETs use the rendered public page
      // as the single source for both theme defaults and saved overrides.
      if (!hasErrors) {
        field.value = formatPageContent(content);
      }

      if (status) {
        status.textContent = hasErrors
          ? "Correct the highlighted content and save again."
          : customized
            ? "Loaded the saved custom content for this page."
            : "Loaded the current theme content. Saving creates a durable custom override.";
      }
    } catch (error) {
      console.error("Unable to load current page content", error);
      setUnavailable(
        "The current page content could not be loaded. Use View current page and try again."
      );
    }
  });
})();

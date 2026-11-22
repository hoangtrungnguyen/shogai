import { useMemo } from "react";
import ReactMarkdown, { defaultUrlTransform, type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import { mediaUrl } from "../api";
import type { SourceRecord } from "../types";

interface Props {
  text: string;
  /** Person slug, used to resolve relative media links (media/x.jpg, ../media/x.jpg). */
  slug?: string;
  sources?: Map<string, SourceRecord>;
  onCite?: (sourceId: string) => void;
  className?: string;
}

// [s3] or [s1, s4] -> one link per id, rendered as citation chips.
const CITE_RE = /\[\s*(s\d+(?:\s*[,;]\s*s\d+)*)\s*\]/gi;

function prepare(text: string): string {
  return text.replace(CITE_RE, (_m, ids: string) =>
    ids
      .split(/[,;]/)
      .map((raw) => {
        const id = raw.trim().toLowerCase();
        return `[${id}](#cite-${id})`;
      })
      .join(" "),
  );
}

export function Markdown({ text, slug, sources, onCite, className }: Props) {
  const prepared = useMemo(() => prepare(text), [text]);

  const components = useMemo<Components>(
    () => ({
      a({ href, children }) {
        if (href?.startsWith("#cite-")) {
          const id = href.slice(6);
          const src = sources?.get(id);
          return (
            <button
              type="button"
              className="cite"
              title={src ? `${src.title}${src.summary ? " — " + src.summary : ""}` : `Source ${id}`}
              onClick={() => onCite?.(id)}
            >
              {id}
            </button>
          );
        }
        const sourceFile = href?.match(/(?:^|\/)sources\/s0*(\d+)-/i);
        if (sourceFile && onCite) {
          return (
            <button type="button" className="link-btn" onClick={() => onCite(`s${sourceFile[1]}`)}>
              {children}
            </button>
          );
        }
        const external = !!href && /^https?:/i.test(href);
        return (
          <a href={href} {...(external ? { target: "_blank", rel: "noreferrer noopener" } : {})}>
            {children}
          </a>
        );
      },
      img({ src, alt }) {
        return <img src={typeof src === "string" ? src : undefined} alt={alt ?? ""} loading="lazy" />;
      },
      table({ children }) {
        return (
          <div className="table-wrap">
            <table>{children}</table>
          </div>
        );
      },
    }),
    [sources, onCite],
  );

  return (
    <div className={`md ${className ?? ""}`}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={components}
        urlTransform={(url) => {
          if (slug && /^(\.\.\/)?media\//.test(url)) return mediaUrl(slug, url);
          return defaultUrlTransform(url);
        }}
      >
        {prepared}
      </ReactMarkdown>
    </div>
  );
}

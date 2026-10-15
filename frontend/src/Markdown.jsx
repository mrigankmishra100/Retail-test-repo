import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import './markdown.css';

// Raw HTML is never executed. Remote images are omitted to avoid tracking requests.
export default function Markdown({ children, className = '' }) {
  return <div className={`rendered-markdown ${className}`}><ReactMarkdown remarkPlugins={[remarkGfm]} skipHtml
    components={{ img: ({ alt }) => <span>{alt || 'Image omitted'}</span>,
      a: ({ children, href }) => <a href={href} target="_blank" rel="noopener noreferrer">{children}</a>,
      table: ({ children }) => <div className="markdown-table"><table>{children}</table></div>,
    }}>{String(children || '')}</ReactMarkdown></div>;
}

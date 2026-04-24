interface Props {
  title: string
  body: string
  onClose: () => void
}

export default function InfoModal({ title, body, onClose }: Props) {
  const paragraphs = body.split('\n\n').filter(Boolean)

  return (
    <div
      className="fixed inset-0 bg-black/70 z-50 flex items-end justify-center"
      onClick={onClose}
    >
      <div
        className="bg-card border border-border rounded-t-2xl w-full max-w-lg p-6 pb-8"
        onClick={e => e.stopPropagation()}
      >
        <div className="flex items-start justify-between mb-4">
          <h3 className="font-bold text-white text-base pr-4">{title}</h3>
          <button
            onClick={onClose}
            className="text-slate-400 hover:text-white text-lg leading-none flex-shrink-0"
          >
            ✕
          </button>
        </div>
        <div className="space-y-3">
          {paragraphs.map((p, i) => (
            <p key={i} className="text-slate-300 text-sm leading-relaxed">{p}</p>
          ))}
        </div>
      </div>
    </div>
  )
}

import { useState } from "react"

interface Props {
  category: string
  onSave: (value: string) => void
  className?: string
}

export default function CategoryInput({ category, onSave, className = "" }: Props) {
  const [editing, setEditing] = useState(false)
  const [value, setValue] = useState(category)

  const save = async () => {
    await onSave(value.trim())
    setEditing(false)
  }

  return editing ? (
    <input
      value={value}
      onChange={(e) => setValue(e.target.value)}
      className={`px-2 py-1 text-[10px] bg-white border border-line rounded focus:outline-none focus:border-primary ${className}`}
      onKeyDown={(e) => {
        if (e.key === "Enter") save()
        if (e.key === "Escape") setEditing(false)
      }}
      autoFocus
    />
  ) : (
    <button
      onClick={() => { setEditing(true); setValue(category) }}
      className="text-[10px] text-muted/30 hover:text-ink transition-colors font-bold uppercase tracking-widest"
    >
      {category || "分类"}
    </button>
  )
}

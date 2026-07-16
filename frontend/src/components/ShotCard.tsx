import { memo } from "react"
import type { ShotProgressInfo } from "@/types"
import { getFrameUrl } from "@/lib/utils"

interface Props {
  shot: ShotProgressInfo
  isComplete: boolean
}

function ShotCard({ shot, isComplete }: Props) {
  const frameUrl = getFrameUrl(shot.keyframe_paths)

  return (
    <div className={`flex items-center gap-3 px-3 py-2 rounded transition-colors group ${
      isComplete ? "bg-surface/50" : "bg-paper/30"
    }`}>
      {/* Icon/Thumbnail area */}
      <div className="w-10 h-7 rounded bg-primary-soft/30 overflow-hidden flex-shrink-0 border border-line/10">
        {frameUrl ? (
          <img
            src={frameUrl}
            alt={`镜${shot.shot_number}`}
            loading="lazy"
            decoding="async"
            className="w-full h-full object-cover"
          />
        ) : (
          <div className="w-full h-full flex items-center justify-center text-xs text-muted">
            {isComplete ? "无图" : "..."}
          </div>
        )}
      </div>

      <div className="flex-1 min-w-0 flex items-center gap-2 text-xs">
        <span className="text-muted">镜</span>
        <span className="text-ink font-medium w-6">#{shot.shot_number}</span>
        <span className="text-line">/</span>
        <span className="text-muted w-10">{shot.start_time_sec.toFixed(1)}s</span>
        <span className="text-line">/</span>
        <div className="flex-1 truncate">
          {isComplete ? (
            shot.analysis_text ? (
              <span className="text-muted">{shot.analysis_text}</span>
            ) : (
              <span className="text-sage font-medium">已分析</span>
            )
          ) : (
            <span className="text-muted animate-pulse italic">分析中...</span>
          )}
        </div>
      </div>
    </div>
  )
}

export default memo(ShotCard)

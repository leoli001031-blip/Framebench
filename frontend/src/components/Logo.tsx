import logoImg from "/logo.png"

export default function Logo({ className = "" }: { className?: string }) {
  return (
    <img
      src={logoImg}
      alt="zc signature"
      className={`object-contain ${className}`}
      style={{ filter: "brightness(0.8) sepia(0.2) saturate(1.2) hue-rotate(-10deg)" }}
    />
  )
}

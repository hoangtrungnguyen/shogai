import { useState } from "react";
import { mediaUrl } from "../api";
import { initials, inkColor } from "../util";

interface Props {
  person: { slug: string; name: string; avatar: string | null };
  size?: number;
}

export function Avatar({ person, size = 40 }: Props) {
  const [broken, setBroken] = useState(false);
  const style = { width: size, height: size, fontSize: Math.round(size * 0.38) };
  if (person.avatar && !broken) {
    return (
      <img
        className="avatar"
        src={mediaUrl(person.slug, person.avatar)}
        style={style}
        alt=""
        onError={() => setBroken(true)}
      />
    );
  }
  return (
    <span className="avatar mono" style={{ ...style, ["--avatar-ink" as string]: inkColor(person.name) }} aria-hidden="true">
      {initials(person.name)}
    </span>
  );
}

import type { ReactNode, SVGProps } from "react";

type IconProps = SVGProps<SVGSVGElement>;

/** Line icons on a 24px grid; they take the text colour and are decorative. */
function Icon({ children, className = "size-6", ...rest }: IconProps & { children: ReactNode }) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.8}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
      className={`shrink-0 ${className}`}
      {...rest}
    >
      {children}
    </svg>
  );
}

export const HomeIcon = (p: IconProps) => (
  <Icon {...p}>
    <path d="M4 11.2 12 4l8 7.2" />
    <path d="M6 9.8V20h12V9.8" />
    <path d="M10 20v-5.5h4V20" />
  </Icon>
);

export const ChatsIcon = (p: IconProps) => (
  <Icon {...p}>
    <path d="M20 12.2c0 3.9-3.6 7-8 7-1 0-2-.2-2.9-.5L5 20l.9-3.3C4.7 15.5 4 13.9 4 12.2c0-3.9 3.6-7 8-7s8 3.1 8 7Z" />
  </Icon>
);

export const BooksIcon = (p: IconProps) => (
  <Icon {...p}>
    <path d="M5 4h4v16H5z" />
    <path d="M9 4h4v16H9z" />
    <path d="m14.3 6.3 3.6-1 3.1 13.7-3.6 1z" />
  </Icon>
);

export const SearchIcon = (p: IconProps) => (
  <Icon {...p}>
    <circle cx="11" cy="11" r="6.5" />
    <path d="m16 16 4.5 4.5" />
  </Icon>
);

export const DownloadIcon = (p: IconProps) => (
  <Icon {...p}>
    <path d="M12 4v11" />
    <path d="m7.5 11 4.5 4.5 4.5-4.5" />
    <path d="M5 20h14" />
  </Icon>
);

export const FolderIcon = (p: IconProps) => (
  <Icon {...p}>
    <path d="M3.5 7.5a2 2 0 0 1 2-2h3.6l2 2.3h7.4a2 2 0 0 1 2 2v7.7a2 2 0 0 1-2 2h-13a2 2 0 0 1-2-2z" />
  </Icon>
);

export const SettingsIcon = (p: IconProps) => (
  <Icon {...p}>
    <path d="M4 7h9" />
    <path d="M17 7h3" />
    <circle cx="15" cy="7" r="2" />
    <path d="M4 17h3" />
    <path d="M11 17h9" />
    <circle cx="9" cy="17" r="2" />
  </Icon>
);

export const RefreshIcon = (p: IconProps) => (
  <Icon {...p}>
    <path d="M19.5 12a7.5 7.5 0 1 1-2.2-5.3" />
    <path d="M19.5 4.5v4h-4" />
  </Icon>
);

export const CloseIcon = (p: IconProps) => (
  <Icon {...p}>
    <path d="m6 6 12 12" />
    <path d="M18 6 6 18" />
  </Icon>
);

export const ArrowDownIcon = (p: IconProps) => (
  <Icon {...p}>
    <path d="M12 5v14" />
    <path d="m6 13 6 6 6-6" />
  </Icon>
);

/** Points back the way the text reads from: right in Arabic. */
export const BackIcon = (p: IconProps) => (
  <Icon {...p}>
    <path d="M5 12h14" />
    <path d="m13 6 6 6-6 6" />
  </Icon>
);

export const ImageIcon = (p: IconProps) => (
  <Icon {...p}>
    <rect x="4" y="5" width="16" height="14" rx="2.5" />
    <circle cx="9" cy="10" r="1.6" />
    <path d="m5 17 4.5-4.5 3.5 3.5 2.5-2.5L20 16" />
  </Icon>
);

export const VideoIcon = (p: IconProps) => (
  <Icon {...p}>
    <rect x="4" y="5" width="16" height="14" rx="2.5" />
    <path d="m10.5 9.5 4 2.5-4 2.5z" />
  </Icon>
);

export const MusicIcon = (p: IconProps) => (
  <Icon {...p}>
    <path d="M9 17.5V6l10-2v11.5" />
    <circle cx="6.5" cy="17.5" r="2.5" />
    <circle cx="16.5" cy="15.5" r="2.5" />
  </Icon>
);

export const ArchiveIcon = (p: IconProps) => (
  <Icon {...p}>
    <path d="M4 8h16v10.5a1.5 1.5 0 0 1-1.5 1.5h-13A1.5 1.5 0 0 1 4 18.5z" />
    <path d="M3 4.5h18V8H3z" />
    <path d="M10 12h4" />
  </Icon>
);

export const ChevronIcon = (p: IconProps) => (
  <Icon {...p}>
    <path d="m6 9.5 6 6 6-6" />
  </Icon>
);

export const CheckIcon = (p: IconProps) => (
  <Icon {...p}>
    <path d="m5 12.5 4.5 4.5L19 7.5" />
  </Icon>
);

type ButtonStyleVariant = "default" | "destructive" | "outline";

interface ButtonStyleResult {
  variant: ButtonStyleVariant;
  className: string;
}

const BUTTON_STYLE_MAP: Record<string, ButtonStyleResult> = {
  primary: {
    variant: "default",
    className: "bg-blue-600 text-white hover:bg-blue-700 nw-btn nw-btn--primary",
  },
  success: {
    variant: "default",
    className: "bg-green-600 text-white hover:bg-green-700 nw-btn nw-btn--success",
  },
  info: {
    variant: "default",
    className: "bg-sky-600 text-white hover:bg-sky-700 nw-btn nw-btn--info",
  },
  warning: {
    variant: "default",
    className: "bg-amber-500 text-white hover:bg-amber-600 nw-btn nw-btn--warning",
  },
  danger: {
    variant: "destructive",
    className: "nw-btn nw-btn--danger",
  },
  "": {
    variant: "outline",
    className: "nw-btn",
  },
};

const DEFAULT_STYLE: ButtonStyleResult = { variant: "outline", className: "nw-btn" };

export function getButtonStyle(buttonStyle: string): ButtonStyleResult {
  return BUTTON_STYLE_MAP[buttonStyle] ?? DEFAULT_STYLE;
}

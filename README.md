# Backgrounds and film

This branch holds only the backgrounds and the film.

## backgrounds/

`backgrounds.css` is one file of plain CSS with inline SVG, holding both Command Studio backgrounds in light and dark. It uses no images and no scripts, so you can copy it into any app.

```html
<link rel="stylesheet" href="backgrounds.css">

<div class="bg-fluted">   …your page…  </div>   <!-- the calm one -->
<div class="bg-currents"> …your page…  </div>
```

- Put the class on the element that holds the page content, so the background is as tall as the content and scrolls with it.
- **Dark mode:** add `class="dark"` or `data-theme="dark"` to that element or to any ancestor, such as `<html>`. To follow the system setting instead, add `bg-auto` as well (`class="bg-fluted bg-auto"`).
- The design is laid out at 1440 px wide and stretches to any width.

The two designs:

- **Fluted** is the calm one, meant for screens used all day. It has three soft colour pools seen through fine vertical ribs, like reeded glass. Light mode is navy and azure with a little peach. Dark mode is midnight `#02070A` with royal blue `#3070E6`, which deliberately avoids the teal-leaning `#0297DE`, plus a trace of cantaloupe.
- **Currents** has a blue current across the middle and a soft swell at the bottom, each with a thin lit line along its top. Light mode is navy and azure with pale peach. Dark mode is midnight with electric blue `#0297DE` and cantaloupe `#FC734D`.

The backgrounds are made to sit behind glass cards, for example `background: rgb(255 255 255 / .62); backdrop-filter: blur(8px) saturate(170%)`, or `rgb(10 16 36 / .78)` in dark mode.

## presentation/

`one-engine-many-uses.html`: the One Engine, Many Uses presenter film. It is one offline file: open it in a browser.

- **F** or double-click: full screen
- **→** / **←**: next and previous scene
- **Space**: pause
- **P**: presenter mode, which holds at each talking point until you press →

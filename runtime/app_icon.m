// app_icon.m - draws NFSMW.app's icon (a generic speedometer, no game artwork) as a 1024x1024 PNG.
//     clang -fobjc-arc app_icon.m -framework Cocoa -o app_icon && ./app_icon icon.png      (runtime/bundle.sh does this)
#import <Cocoa/Cocoa.h>
#import <ImageIO/ImageIO.h>
#import <UniformTypeIdentifiers/UniformTypeIdentifiers.h>

static CGColorRef rgb(double r, double g, double b, double a) { return CGColorCreateSRGB(r, g, b, a); }

int main(int argc, char **argv) {
    if (argc < 2) { fprintf(stderr, "usage: app_icon <out.png>\n"); return 1; }
    const int S = 1024;
    CGColorSpaceRef cs = CGColorSpaceCreateWithName(kCGColorSpaceSRGB);
    CGContextRef g = CGBitmapContextCreate(NULL, S, S, 8, 0, cs, kCGImageAlphaPremultipliedLast);
    // Big Sur grid: an 824 pt rounded square centred on the canvas, corner radius ~185.
    CGRect tile = CGRectMake(100, 100, 824, 824);
    CGPathRef shape = CGPathCreateWithRoundedRect(tile, 185, 185, NULL);
    CGContextSaveGState(g);
    CGContextSetShadowWithColor(g, CGSizeMake(0, -12), 28, rgb(0, 0, 0, 0.45));
    CGContextAddPath(g, shape);
    CGContextSetFillColorWithColor(g, rgb(0.05, 0.06, 0.09, 1));
    CGContextFillPath(g);
    CGContextRestoreGState(g);
    CGContextAddPath(g, shape);
    CGContextClip(g);
    CGColorRef bg[] = {rgb(0.16, 0.19, 0.27, 1), rgb(0.04, 0.05, 0.08, 1)};
    CFArrayRef bga = CFArrayCreate(NULL, (const void **)bg, 2, NULL);
    CGGradientRef bgg = CGGradientCreateWithColors(cs, bga, NULL);
    CGContextDrawLinearGradient(g, bgg, CGPointMake(0, 924), CGPointMake(0, 100), 0);

    // Gauge: a 240-degree dial from lower left (210 deg) clockwise to lower right (-30 deg), centre a bit low.
    const CGPoint c = CGPointMake(512, 470);
    const double R = 300, a0 = 210 * M_PI / 180, a1 = -30 * M_PI / 180;
    CGContextSetLineCap(g, kCGLineCapRound);
    CGContextSetLineWidth(g, 44);
    CGContextSetStrokeColorWithColor(g, rgb(1, 1, 1, 0.10));
    CGContextAddArc(g, c.x, c.y, R, a0, a1, 1);
    CGContextStrokePath(g);
    // the lit part, amber to red, up to the needle
    const double needle = a0 + (a1 - a0) * 0.82;
    CGContextSaveGState(g);
    CGContextAddArc(g, c.x, c.y, R, a0, needle, 1);
    CGContextReplacePathWithStrokedPath(g);
    CGContextClip(g);
    CGColorRef hot[] = {rgb(1.0, 0.78, 0.18, 1), rgb(1.0, 0.42, 0.10, 1), rgb(0.93, 0.13, 0.16, 1)};
    CFArrayRef hota = CFArrayCreate(NULL, (const void **)hot, 3, NULL);
    CGGradientRef hotg = CGGradientCreateWithColors(cs, hota, NULL);
    CGContextDrawLinearGradient(g, hotg, CGPointMake(c.x - R, c.y), CGPointMake(c.x + R, c.y), 0);
    CGContextRestoreGState(g);
    // ticks
    for (int i = 0; i <= 12; ++i) {
        double a = a0 + (a1 - a0) * i / 12.0, r0 = R - 72, r1 = R - (i % 2 ? 52 : 40);
        CGContextSetLineWidth(g, i % 2 ? 9 : 16);
        CGContextSetStrokeColorWithColor(g, rgb(1, 1, 1, i % 2 ? 0.45 : 0.8));
        CGContextMoveToPoint(g, c.x + r0 * cos(a), c.y + r0 * sin(a));
        CGContextAddLineToPoint(g, c.x + r1 * cos(a), c.y + r1 * sin(a));
        CGContextStrokePath(g);
    }
    // needle and hub
    CGContextSaveGState(g);
    CGContextSetShadowWithColor(g, CGSizeMake(0, -6), 16, rgb(0, 0, 0, 0.6));
    double nx = cos(needle), ny = sin(needle), px = -ny, py = nx;
    CGContextMoveToPoint(g, c.x + nx * (R - 46), c.y + ny * (R - 46));
    CGContextAddLineToPoint(g, c.x + px * 22 - nx * 40, c.y + py * 22 - ny * 40);
    CGContextAddLineToPoint(g, c.x - px * 22 - nx * 40, c.y - py * 22 - ny * 40);
    CGContextClosePath(g);
    CGContextSetFillColorWithColor(g, rgb(0.97, 0.97, 0.98, 1));
    CGContextFillPath(g);
    CGContextAddEllipseInRect(g, CGRectMake(c.x - 46, c.y - 46, 92, 92));
    CGContextSetFillColorWithColor(g, rgb(0.93, 0.13, 0.16, 1));
    CGContextFillPath(g);
    CGContextRestoreGState(g);
    CGContextAddEllipseInRect(g, CGRectMake(c.x - 18, c.y - 18, 36, 36));
    CGContextSetFillColorWithColor(g, rgb(0.05, 0.06, 0.09, 1));
    CGContextFillPath(g);
    // a thin highlight along the tile's top edge
    CGContextResetClip(g);
    CGContextAddPath(g, shape);
    CGContextSetLineWidth(g, 3);
    CGContextSetStrokeColorWithColor(g, rgb(1, 1, 1, 0.12));
    CGContextStrokePath(g);

    CGImageRef img = CGBitmapContextCreateImage(g);
    NSURL *url = [NSURL fileURLWithPath:[NSString stringWithUTF8String:argv[1]]];
    CGImageDestinationRef d = CGImageDestinationCreateWithURL((__bridge CFURLRef)url, (__bridge CFStringRef)UTTypePNG.identifier, 1, NULL);
    CGImageDestinationAddImage(d, img, NULL);
    return CGImageDestinationFinalize(d) ? 0 : 1;
}

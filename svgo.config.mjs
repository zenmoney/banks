// Use with the installed compatible SVGO CLI; record its version in trial evidence.
/** @type {import('svgo').Config} */
export default {
    multipass: true,
    floatPrecision: 4,
    js2svg: {
        indent: 4,
        pretty: true,
    },
    plugins: [
        // Defense in depth; source safety is checked before optimization.
        'removeScripts',
        {
            name: 'preset-default',
            params: {
                overrides: {
                    inlineStyles: {
                        onlyMatchedOnce: false,
                    },
                    convertPathData: {
                        floatPrecision: 4,
                        transformPrecision: 5,
                    },
                    convertTransform: {
                        floatPrecision: 4,
                        transformPrecision: 5,
                    },
                    removeDesc: false,
                },
            },
        },
        'convertStyleToAttrs',
    ],
};
